import asyncio
import json
from datetime import datetime, timezone
import redis.asyncio as redis
from typing import Dict, Any, Optional

from app.core.config import settings
from app.core.logging import logger
from app.services.exchange_service import exchange_service
from app.db.supabase import supabase_manager
from app.db.mongo import mongo_manager

class TradeManager:
    def __init__(self):
        self.redis_client = None
        self.monitor_task = None
        self.running = False

    async def get_redis(self):
        if self.redis_client is None:
            self.redis_client = await redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self.redis_client

    async def start_monitoring(self):
        """
        Starts the background loop for monitoring active positions.
        """
        if not self.running:
            self.running = True
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("Background trade monitoring loop started.")

    async def stop_monitoring(self):
        self.running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
            logger.info("Background trade monitoring loop stopped.")

    async def calculate_position_size(self, symbol: str, entry: float, stop_loss: float) -> Dict[str, Any]:
        """
        Sizes the trade dynamically based on balance, risk pct, and SL distance.
        Falls back to safe minimums if exchange balance fetching fails.
        """
        try:
            await exchange_service.initialize()
            is_simulated = (settings.BINANCE_API_KEY is None or settings.BINANCE_API_SECRET is None)
            
            if is_simulated:
                # Default mock sizing
                qty = 0.01 if "BTC" in symbol else 0.1 if "ETH" in symbol else 1.0
                return {"quantity": qty, "risk_amount_usdt": 10.0, "is_simulated": True}

            balance = await exchange_service.exchange.fetch_balance()
            usdt_free = float(balance.get('free', {}).get('USDT', 0.0))
            
            if usdt_free <= 10.0:
                logger.warning(f"Low free USDT balance: {usdt_free}. Using minimum default size.")
                qty = 0.002 if "BTC" in symbol else 0.02 if "ETH" in symbol else 0.5
                return {"quantity": qty, "risk_amount_usdt": 2.0, "is_simulated": False}

            # Size calculation
            risk_pct = settings.RISK_PERCENTAGE
            risk_amount = usdt_free * (risk_pct / 100.0)
            
            sl_distance_pct = abs(entry - stop_loss) / entry
            if sl_distance_pct == 0:
                sl_distance_pct = 0.01 # prevent division by zero
                
            notional = risk_amount / sl_distance_pct
            raw_qty = notional / entry

            # Precision formatting via CCXT
            market = await exchange_service.exchange.market(symbol)
            qty = float(exchange_service.exchange.amount_to_precision(symbol, raw_qty))
            
            logger.info(f"Position Sizing for {symbol}: Balance={usdt_free:.2f} USDT, Risk={risk_amount:.2f} USDT, Size={qty} contracts")
            return {"quantity": qty, "risk_amount_usdt": risk_amount, "is_simulated": False}
        except Exception as e:
            logger.error(f"Error in position sizing: {e}. Falling back to default mock sizes.")
            qty = 0.005 if "BTC" in symbol else 0.05 if "ETH" in symbol else 1.0
            return {"quantity": qty, "risk_amount_usdt": 5.0, "is_simulated": True}

    async def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        analysis_snapshot: Optional[dict] = None,
        decision_snapshot: Optional[dict] = None
    ) -> Dict[str, Any]:
        """
        Coordinates full trade execution: balance check, order submission, DB snapshotting, and Redis registration.
        """
        logger.info(f"Starting trade execution pipeline for {symbol} ({direction})")
        r_client = await self.get_redis()
        
        # Check if already active
        active_key = f"active_trade:{symbol}"
        if await r_client.exists(active_key):
            logger.warning(f"Active trade already exists for {symbol}. Skipping execution.")
            return {"status": "error", "message": "Active trade already exists"}

        # Calculate sizing
        sizing = await self.calculate_position_size(symbol, entry, stop_loss)
        qty = sizing["quantity"]
        is_simulated = sizing["is_simulated"] or settings.BINANCE_API_KEY is None
        mode = "TESTNET" if (is_simulated or settings.BINANCE_USE_TESTNET) else "LIVE"

        # Build trade dict
        trade_id = str(uuid_v4())
        trade_record = {
            "id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "status": "ACTIVE",
            "mode": mode,
            "entry_price": entry,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "quantity": qty,
            "leverage": settings.DEFAULT_LEVERAGE,
            "risk_pct": settings.RISK_PERCENTAGE,
            "executed_at": datetime.now(timezone.utc),
            "close_reason": None,
            "final_pnl": None
        }

        order_details = {}
        
        if is_simulated:
            logger.info(f"[SIMULATION] Mocking orders for {symbol} {direction}")
            order_details = {
                "entry_order_id": f"mock_entry_{trade_id[:8]}",
                "sl_order_id": f"mock_sl_{trade_id[:8]}",
                "tp1_order_id": f"mock_tp1_{trade_id[:8]}",
                "tp2_order_id": f"mock_tp2_{trade_id[:8]}",
            }
        else:
            try:
                # Set Leverage
                try:
                    await exchange_service.exchange.set_leverage(settings.DEFAULT_LEVERAGE, symbol)
                except Exception as e:
                    logger.warning(f"Failed to set leverage: {e}. Continuing order placement.")

                side = "buy" if direction == "LONG" else "sell"
                opp_side = "sell" if direction == "LONG" else "buy"

                # 1. Entry order (Market)
                entry_order = await exchange_service.create_market_order(symbol, side, qty)
                entry_price_actual = float(entry_order.get("average") or entry_order.get("price") or entry)
                trade_record["entry_price"] = entry_price_actual
                order_details["entry_order_id"] = entry_order.get("id")

                # 2. Stop Loss (Stop Market)
                sl_order = await exchange_service.create_stop_market_order(symbol, opp_side, qty, stop_loss)
                order_details["sl_order_id"] = sl_order.get("id")

                # 3. TP1 (Limit - 50%)
                qty1 = float(exchange_service.exchange.amount_to_precision(symbol, qty / 2))
                tp1_order = await exchange_service.create_limit_order(symbol, opp_side, qty1, take_profit_1)
                order_details["tp1_order_id"] = tp1_order.get("id")

                # 4. TP2 (Limit - Remaining 50%)
                qty2 = float(exchange_service.exchange.amount_to_precision(symbol, qty - qty1))
                tp2_order = await exchange_service.create_limit_order(symbol, opp_side, qty2, take_profit_2)
                order_details["tp2_order_id"] = tp2_order.get("id")

            except Exception as e:
                logger.error(f"Execution failed on Binance: {e}. Cancelling placement.")
                await exchange_service.cancel_orders_for_symbol(symbol)
                return {"status": "error", "message": f"Binance orders failed: {str(e)}"}

        # Write to Database
        await supabase_manager.create_trade_record(trade_record)
        await supabase_manager.log_trade_event(trade_id, "ENTRY_FILLED", trade_record["entry_price"], {"order_details": order_details})
        
        # Save snapshots in Mongo
        if analysis_snapshot:
            await mongo_manager.save_snapshot(trade_id, symbol, "analysis", analysis_snapshot)
        if decision_snapshot:
            await mongo_manager.save_snapshot(trade_id, symbol, "decision", decision_snapshot)
            reason = decision_snapshot.get("reason", "")
            await mongo_manager.save_execution_reasoning(trade_id, reason)

        # Register in Redis for loop monitoring
        redis_state = {
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": trade_record["entry_price"],
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "quantity": qty,
            "sl_order_id": order_details.get("sl_order_id"),
            "order_details": order_details,
            "is_simulated": is_simulated,
            "sl_at_be": False,
            "tp1_filled": False,
            "executed_at": trade_record["executed_at"].isoformat() if isinstance(trade_record["executed_at"], datetime) else trade_record["executed_at"]
        }
        await r_client.set(active_key, json.dumps(redis_state))
        
        logger.info(f"Trade successfully established in database and monitored in Redis for {symbol}.")
        return {
            "status": "success",
            "trade_id": trade_id,
            "is_simulated": is_simulated,
            "qty": qty,
            "entry_price": trade_record["entry_price"]
        }

    async def manual_close_position(self, symbol: str) -> Dict[str, Any]:
        """
        Manually closes any open trade/position for the symbol, cancelling all bracket orders.
        """
        logger.info(f"Manual/Emergency close requested for {symbol}")
        r_client = await self.get_redis()
        active_key = f"active_trade:{symbol}"
        
        state_str = await r_client.get(active_key)
        if not state_str:
            return {"status": "error", "message": "No active trade registered for this symbol."}
        
        state = json.loads(state_str)
        trade_id = state["trade_id"]
        is_simulated = state["is_simulated"]

        if not is_simulated:
            try:
                # Cancel orders
                await exchange_service.cancel_orders_for_symbol(symbol)
                # Place counter market order to close position
                opp_side = "sell" if state["direction"] == "LONG" else "buy"
                await exchange_service.create_market_order(symbol, opp_side, state["quantity"])
                logger.info(f"Binance orders closed and position market-exited for {symbol}")
            except Exception as e:
                logger.error(f"Error during Binance close for {symbol}: {e}")

        # Calculate actual PNL at current price
        current_price = state["entry_price"]
        try:
            ticker = await exchange_service.fetch_ticker(symbol)
            current_price = float(ticker.get("close") or ticker.get("last") or state["entry_price"])
        except Exception as e:
            logger.warning(f"Could not fetch live price for close PnL calculation: {e}")

        pnl_pct = (current_price - state["entry_price"]) / state["entry_price"]
        if state["direction"] == "SHORT":
            pnl_pct = -pnl_pct
        pnl_usdt = pnl_pct * state["entry_price"] * state["quantity"]

        # Update Supabase trade to CLOSED
        close_time = datetime.now(timezone.utc)
        await supabase_manager.update_trade_record(trade_id, {
            "status": "CLOSED",
            "closed_at": close_time,
            "close_reason": "MANUAL_CLOSE",
            "final_pnl": round(pnl_usdt, 4)
        })
        await supabase_manager.log_trade_event(trade_id, "FULL_CLOSE", current_price, {"reason": "MANUAL_CLOSE", "pnl": pnl_usdt})

        # Delete active trace in Redis
        await r_client.delete(active_key)
        logger.info(f"Trade {trade_id} successfully closed and deregistered.")
        return {"status": "success", "message": "Position successfully closed."}

    async def _monitor_loop(self):
        """
        Background loop iterating over registered trades, validating price levels or exchange orders.
        """
        while self.running:
            try:
                r_client = await self.get_redis()
                keys = await r_client.keys("active_trade:*")
                for key in keys:
                    try:
                        state_str = await r_client.get(key)
                        if not state_str:
                            continue
                        state = json.loads(state_str)
                        await self._check_trade_state(state, key)
                    except Exception as loop_err:
                        logger.error(f"Error processing key {key} in monitor loop: {loop_err}")
            except Exception as e:
                logger.error(f"Error in monitor loop shell: {e}")
            await asyncio.sleep(5) # Poll every 5s

    async def _check_trade_state(self, state: dict, redis_key: str):
        symbol = state["symbol"]
        trade_id = state["trade_id"]
        direction = state["direction"]
        entry = state["entry_price"]
        sl = state["stop_loss"]
        tp1 = state["take_profit_1"]
        tp2 = state["take_profit_2"]
        is_simulated = state["is_simulated"]
        
        # 1. Fetch current price to check fill levels (essential for simulated, secondary check for live)
        current_price = entry
        try:
            # We can use fetch_ohlcv to get last close or tickers
            ticker = await exchange_service.fetch_ticker(symbol)
            current_price = float(ticker.get("close") or ticker.get("last") or entry)
        except Exception as e:
            logger.warning(f"Could not fetch live price for {symbol}: {e}. Skipping check.")
            return

        opp_side = "sell" if direction == "LONG" else "buy"
        r_client = await self.get_redis()

        # Check conditions
        tp1_hit = (direction == "LONG" and current_price >= tp1) or (direction == "SHORT" and current_price <= tp1)
        tp2_hit = (direction == "LONG" and current_price >= tp2) or (direction == "SHORT" and current_price <= tp2)
        sl_hit = (direction == "LONG" and current_price <= sl) or (direction == "SHORT" and current_price >= sl)

        # --- A. Check TP1 (SL to Break-Even) ---
        if tp1_hit and not state["tp1_filled"]:
            logger.info(f"TP1 hit for {symbol} @ {current_price}. Moving Stop Loss to Break-Even ({entry})")
            state["tp1_filled"] = True
            
            success = True
            if not is_simulated:
                try:
                    # Cancel old SL
                    old_sl_id = state["order_details"].get("sl_order_id") or state.get("sl_order_id")
                    if old_sl_id:
                        await exchange_service.exchange.cancel_order(old_sl_id, symbol)
                    
                    # Create new Stop Loss at Entry
                    new_sl_order = await exchange_service.create_stop_market_order(symbol, opp_side, state["quantity"], entry)
                    new_sl_id = new_sl_order.get("id")
                    state["order_details"]["sl_order_id"] = new_sl_id
                    state["sl_order_id"] = new_sl_id
                except Exception as ex:
                    logger.error(f"Failed to adjust SL order on exchange for {symbol}: {ex}")
                    success = False
            else:
                new_sl_id = f"mock_sl_be_{trade_id[:8]}"
                state["order_details"]["sl_order_id"] = new_sl_id
                state["sl_order_id"] = new_sl_id

            if success:
                state["stop_loss"] = entry
                state["sl_at_be"] = True
                
                # Save DB log
                await supabase_manager.log_trade_event(trade_id, "PARTIAL_TP1_FILLED", tp1, {"new_sl": entry})
                await supabase_manager.log_trade_event(trade_id, "SL_UPDATED_TO_BE", entry)
                await supabase_manager.update_trade_record(trade_id, {"stop_loss": entry})
                
                # Immediately write the full updated state back to Redis
                await r_client.set(redis_key, json.dumps(state))
                logger.info(f"Successfully adjusted SL to BE and synced state to Redis for {symbol}")

        # --- B. Check TP2 (Full Close Win) ---
        elif tp2_hit:
            logger.info(f"TP2 hit for {symbol} @ {current_price}. Closing position with success.")
            if not is_simulated:
                await exchange_service.cancel_orders_for_symbol(symbol)
            
            pnl = abs(tp2 - entry) * state["quantity"]
            if direction == "SHORT":
                pnl = -pnl # wait, short PnL is positive when price falls
            # standard PNL calculation:
            pnl_pct = (tp2 - entry) / entry
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            pnl_usdt = pnl_pct * entry * state["quantity"]

            await supabase_manager.update_trade_record(trade_id, {
                "status": "CLOSED",
                "closed_at": datetime.now(timezone.utc),
                "close_reason": "TAKE_PROFIT_2",
                "final_pnl": round(pnl_usdt, 4)
            })
            await supabase_manager.log_trade_event(trade_id, "FULL_CLOSE", tp2, {"reason": "TAKE_PROFIT_2", "pnl": pnl_usdt})
            await r_client.delete(redis_key)

        # --- C. Check SL (Full Close Loss) ---
        elif sl_hit:
            logger.info(f"SL hit for {symbol} @ {current_price}. Closing position with loss.")
            if not is_simulated:
                await exchange_service.cancel_orders_for_symbol(symbol)

            pnl_pct = (sl - entry) / entry
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            pnl_usdt = pnl_pct * entry * state["quantity"]

            await supabase_manager.update_trade_record(trade_id, {
                "status": "CLOSED",
                "closed_at": datetime.now(timezone.utc),
                "close_reason": "STOP_LOSS",
                "final_pnl": round(pnl_usdt, 4)
            })
            await supabase_manager.log_trade_event(trade_id, "FULL_CLOSE", sl, {"reason": "STOP_LOSS", "pnl": pnl_usdt})
            await r_client.delete(redis_key)

def uuid_v4() -> str:
    return str(uuid.uuid4())

import uuid
trade_manager = TradeManager()
