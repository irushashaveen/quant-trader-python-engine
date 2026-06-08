"""
trade_manager.py
================
Trade lifecycle manager: sizing, execution, monitoring, and manual close.

Changes in this version
-----------------------
* ``calculate_position_size`` now accepts an optional ``atr_value`` and
  delegates leverage selection to ``core_math_engine.calculate_dynamic_leverage``
  instead of using a hardcoded ``DEFAULT_LEVERAGE``.

* When ``atr_value`` is provided the SL distance used for sizing is
  ``max(1.5 × ATR, raw_sl_distance)`` — the wider of the two — so the
  position is never sized assuming an unrealistically tight stop.

* ``execute_trade`` accepts and forwards ``atr_value``.

* All database insertion logic (Supabase / MongoDB / Redis) is **unchanged**.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import logger
from app.db.mongo import mongo_manager
from app.db.supabase import supabase_manager
from app.services.core_math_engine import calculate_dynamic_leverage
from app.services.exchange_service import exchange_service


class TradeManager:
    def __init__(self):
        self.redis_client = None
        self.monitor_task = None
        self.running = False

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    async def get_redis(self):
        if self.redis_client is None:
            self.redis_client = await redis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
        return self.redis_client

    # ------------------------------------------------------------------
    # Background monitoring lifecycle
    # ------------------------------------------------------------------

    async def start_monitoring(self):
        """Start the background loop that monitors active positions."""
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

    # ------------------------------------------------------------------
    # Position sizing (dynamic, ATR-aware)
    # ------------------------------------------------------------------

    async def calculate_position_size(
        self,
        symbol: str,
        entry: float,
        stop_loss: float,
        atr_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Size the trade dynamically based on:
          - Account balance (fetched live from exchange)
          - Risk percentage (settings.RISK_PERCENTAGE — default 1%)
          - ATR-based dynamic leverage (replaces hardcoded DEFAULT_LEVERAGE)
          - Effective SL distance = max(raw SL distance, 1.5 × ATR distance)

        Falls back to safe minimums if balance fetching or leverage setting fails.

        Parameters
        ----------
        symbol    : Trading pair (e.g. "BTC/USDT")
        entry     : Proposed entry price
        stop_loss : Stop loss price
        atr_value : Optional ATR value in price units.  When provided, leverage
                    and SL distance are both influenced by volatility.

        Returns
        -------
        dict with keys:
          quantity         – position size in base asset units
          risk_amount_usdt – USDT value risked
          leverage         – leverage that was (or would be) applied
          is_simulated     – True when no live API keys are present
        """
        try:
            await exchange_service.initialize()
            is_simulated = (
                settings.BINANCE_API_KEY is None
                or settings.BINANCE_API_SECRET is None
            )

            # --- Dynamic leverage ---
            if atr_value and atr_value > 0 and entry > 0:
                leverage = calculate_dynamic_leverage(atr_value, entry)
            else:
                # Fallback: use midpoint of allowed range when ATR unavailable
                leverage = max(
                    settings.MIN_LEVERAGE,
                    min(settings.MAX_LEVERAGE, 5),
                )
                logger.warning(
                    "calculate_position_size: ATR not available — "
                    f"falling back to {leverage}× leverage"
                )

            if is_simulated:
                qty = 0.01 if "BTC" in symbol else 0.1 if "ETH" in symbol else 1.0
                return {
                    "quantity": qty,
                    "risk_amount_usdt": 10.0,
                    "leverage": leverage,
                    "is_simulated": True,
                }

            # --- Live balance fetch ---
            balance = await exchange_service.exchange.fetch_balance()
            usdt_free = float(balance.get("free", {}).get("USDT", 0.0))

            if usdt_free <= 10.0:
                logger.warning(
                    f"Low free USDT balance: {usdt_free}. Using minimum default size."
                )
                qty = (
                    0.002 if "BTC" in symbol else 0.02 if "ETH" in symbol else 0.5
                )
                return {
                    "quantity": qty,
                    "risk_amount_usdt": 2.0,
                    "leverage": leverage,
                    "is_simulated": False,
                }

            # --- Risk-based sizing ---
            risk_amount = usdt_free * (settings.RISK_PERCENTAGE / 100.0)

            raw_sl_distance_pct = abs(entry - stop_loss) / entry if entry > 0 else 0.01

            # When ATR is available, ensure the SL distance used for sizing
            # is at least 1.5 × ATR expressed as a % of price.  This prevents
            # oversizing into a stop that is tighter than recent volatility.
            if atr_value and atr_value > 0 and entry > 0:
                atr_sl_distance_pct = (1.5 * atr_value) / entry
                effective_sl_pct = max(raw_sl_distance_pct, atr_sl_distance_pct)
            else:
                effective_sl_pct = raw_sl_distance_pct

            if effective_sl_pct == 0:
                effective_sl_pct = 0.01  # prevent division by zero

            notional = risk_amount / effective_sl_pct
            raw_qty = notional / entry

            # Round to exchange-valid precision
            market = await exchange_service.exchange.market(symbol)
            qty = float(
                exchange_service.exchange.amount_to_precision(symbol, raw_qty)
            )

            logger.info(
                f"Position Sizing — {symbol}: balance={usdt_free:.2f} USDT, "
                f"risk={risk_amount:.2f} USDT, SL_pct={effective_sl_pct:.4%}, "
                f"leverage={leverage}×, size={qty}"
            )
            return {
                "quantity": qty,
                "risk_amount_usdt": risk_amount,
                "leverage": leverage,
                "is_simulated": False,
            }

        except Exception as exc:
            logger.error(
                f"Error in position sizing: {exc}. Falling back to default mock sizes."
            )
            qty = 0.005 if "BTC" in symbol else 0.05 if "ETH" in symbol else 1.0
            fallback_leverage = max(settings.MIN_LEVERAGE, min(settings.MAX_LEVERAGE, 5))
            return {
                "quantity": qty,
                "risk_amount_usdt": 5.0,
                "leverage": fallback_leverage,
                "is_simulated": True,
            }

    # ------------------------------------------------------------------
    # Full trade execution
    # ------------------------------------------------------------------

    async def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        atr_value: Optional[float] = None,
        analysis_snapshot: Optional[dict] = None,
        decision_snapshot: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Coordinate full trade execution: balance check, dynamic leverage,
        order submission, DB snapshotting, and Redis registration.

        The ``atr_value`` parameter threads through to ``calculate_position_size``
        so that leverage and position size reflect current volatility.

        All database insertion logic is unchanged from the previous version.
        """
        logger.info(
            f"Starting trade execution pipeline for {symbol} ({direction})"
        )
        r_client = await self.get_redis()

        # Guard: abort if there is already an active trade for this symbol
        active_key = f"active_trade:{symbol}"
        if await r_client.exists(active_key):
            logger.warning(
                f"Active trade already exists for {symbol}. Skipping execution."
            )
            return {"status": "error", "message": "Active trade already exists"}

        # --- Dynamic sizing (ATR-aware) ---
        sizing = await self.calculate_position_size(
            symbol, entry, stop_loss, atr_value=atr_value
        )
        qty = sizing["quantity"]
        leverage = sizing["leverage"]
        is_simulated = sizing["is_simulated"] or settings.BINANCE_API_KEY is None
        mode = (
            "TESTNET" if (is_simulated or settings.BINANCE_USE_TESTNET) else "LIVE"
        )

        # Build trade record
        trade_id = str(uuid.uuid4())
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
            "leverage": leverage,          # ← dynamic, not hardcoded
            "risk_pct": settings.RISK_PERCENTAGE,
            "executed_at": datetime.now(timezone.utc),
            "close_reason": None,
            "final_pnl": None,
        }

        order_details: Dict[str, Any] = {}

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
                # Set dynamic leverage on exchange
                try:
                    await exchange_service.exchange.set_leverage(leverage, symbol)
                    logger.info(f"Leverage set to {leverage}× for {symbol}")
                except Exception as exc:
                    logger.warning(
                        f"Failed to set leverage ({leverage}×) for {symbol}: {exc}. "
                        "Continuing order placement."
                    )

                side = "buy" if direction == "LONG" else "sell"
                opp_side = "sell" if direction == "LONG" else "buy"

                # 1. Entry (Market)
                entry_order = await exchange_service.create_market_order(
                    symbol, side, qty
                )
                entry_price_actual = float(
                    entry_order.get("average")
                    or entry_order.get("price")
                    or entry
                )
                trade_record["entry_price"] = entry_price_actual
                order_details["entry_order_id"] = entry_order.get("id")

                # 2. Stop Loss (Stop Market)
                sl_order = await exchange_service.create_stop_market_order(
                    symbol, opp_side, qty, stop_loss
                )
                order_details["sl_order_id"] = sl_order.get("id")

                # 3. TP1 (Limit — 50%)
                qty1 = float(
                    exchange_service.exchange.amount_to_precision(symbol, qty / 2)
                )
                tp1_order = await exchange_service.create_limit_order(
                    symbol, opp_side, qty1, take_profit_1
                )
                order_details["tp1_order_id"] = tp1_order.get("id")

                # 4. TP2 (Limit — remaining 50%)
                qty2 = float(
                    exchange_service.exchange.amount_to_precision(symbol, qty - qty1)
                )
                tp2_order = await exchange_service.create_limit_order(
                    symbol, opp_side, qty2, take_profit_2
                )
                order_details["tp2_order_id"] = tp2_order.get("id")

            except Exception as exc:
                logger.error(
                    f"Execution failed on Binance for {symbol}: {exc}. "
                    "Cancelling placement."
                )
                await exchange_service.cancel_orders_for_symbol(symbol)
                return {
                    "status": "error",
                    "message": f"Binance orders failed: {str(exc)}",
                }

        # --- Database writes (unchanged) ---
        await supabase_manager.create_trade_record(trade_record)
        await supabase_manager.log_trade_event(
            trade_id,
            "ENTRY_FILLED",
            trade_record["entry_price"],
            {"order_details": order_details},
        )

        if analysis_snapshot:
            await mongo_manager.save_snapshot(
                trade_id, symbol, "analysis", analysis_snapshot
            )
        if decision_snapshot:
            await mongo_manager.save_snapshot(
                trade_id, symbol, "decision", decision_snapshot
            )
            reason = decision_snapshot.get("reason", "")
            await mongo_manager.save_execution_reasoning(trade_id, reason)

        # --- Redis registration (unchanged schema) ---
        redis_state = {
            "trade_id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": trade_record["entry_price"],
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "quantity": qty,
            "leverage": leverage,
            "sl_order_id": order_details.get("sl_order_id"),
            "order_details": order_details,
            "is_simulated": is_simulated,
            "sl_at_be": False,
            "tp1_filled": False,
            "executed_at": (
                trade_record["executed_at"].isoformat()
                if isinstance(trade_record["executed_at"], datetime)
                else trade_record["executed_at"]
            ),
        }
        await r_client.set(active_key, json.dumps(redis_state))

        logger.info(
            f"Trade successfully established in database and registered in Redis "
            f"for {symbol} — leverage={leverage}×."
        )
        return {
            "status": "success",
            "trade_id": trade_id,
            "is_simulated": is_simulated,
            "qty": qty,
            "leverage": leverage,
            "entry_price": trade_record["entry_price"],
        }

    # ------------------------------------------------------------------
    # Manual / emergency close
    # ------------------------------------------------------------------

    async def manual_close_position(self, symbol: str) -> Dict[str, Any]:
        """
        Manually close any open trade/position for ``symbol``,
        cancelling all bracket orders.
        """
        logger.info(f"Manual/Emergency close requested for {symbol}")
        r_client = await self.get_redis()
        active_key = f"active_trade:{symbol}"

        state_str = await r_client.get(active_key)
        if not state_str:
            return {
                "status": "error",
                "message": "No active trade registered for this symbol.",
            }

        state = json.loads(state_str)
        trade_id = state["trade_id"]
        is_simulated = state["is_simulated"]

        if not is_simulated:
            try:
                await exchange_service.cancel_orders_for_symbol(symbol)
                opp_side = "sell" if state["direction"] == "LONG" else "buy"
                await exchange_service.create_market_order(
                    symbol, opp_side, state["quantity"]
                )
                logger.info(
                    f"Binance orders closed and position market-exited for {symbol}"
                )
            except Exception as exc:
                logger.error(f"Error during Binance close for {symbol}: {exc}")

        # PnL at current price
        current_price = state["entry_price"]
        try:
            ticker = await exchange_service.fetch_ticker(symbol)
            current_price = float(
                ticker.get("close") or ticker.get("last") or state["entry_price"]
            )
        except Exception as exc:
            logger.warning(
                f"Could not fetch live price for close PnL calculation: {exc}"
            )

        pnl_pct = (current_price - state["entry_price"]) / state["entry_price"]
        if state["direction"] == "SHORT":
            pnl_pct = -pnl_pct
        pnl_usdt = pnl_pct * state["entry_price"] * state["quantity"]

        # Supabase update (unchanged)
        close_time = datetime.now(timezone.utc)
        await supabase_manager.update_trade_record(
            trade_id,
            {
                "status": "CLOSED",
                "closed_at": close_time,
                "close_reason": "MANUAL_CLOSE",
                "final_pnl": round(pnl_usdt, 4),
            },
        )
        await supabase_manager.log_trade_event(
            trade_id,
            "FULL_CLOSE",
            current_price,
            {"reason": "MANUAL_CLOSE", "pnl": pnl_usdt},
        )

        await r_client.delete(active_key)
        logger.info(f"Trade {trade_id} successfully closed and deregistered.")
        return {"status": "success", "message": "Position successfully closed."}

    # ------------------------------------------------------------------
    # Background monitoring loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self):
        """Background loop reacting instantly to WebSocket trade updates."""
        from app.services.data_loader import data_loader
        while self.running:
            try:
                r_client = await self.get_redis()
                keys = await r_client.keys("active_trade:*")
                if not keys:
                    # No active positions: sleep to conserve CPU
                    await asyncio.sleep(0.5)
                    continue
                
                # Suspend execution until the next WebSocket trade update occurs
                await data_loader.memory_manager.wait_for_update()
                
                # Re-fetch keys because a trade might have been closed/removed while waiting
                keys = await r_client.keys("active_trade:*")
                for key in keys:
                    try:
                        state_str = await r_client.get(key)
                        if not state_str:
                            continue
                        state = json.loads(state_str)
                        await self._check_trade_state(state, key)
                    except Exception as loop_err:
                        logger.error(
                            f"Error processing key {key} in monitor loop: {loop_err}"
                        )
            except Exception as exc:
                logger.error(f"Error in monitor loop shell: {exc}")
                await asyncio.sleep(1)

    async def _check_trade_state(self, state: dict, redis_key: str):
        symbol = state["symbol"]
        trade_id = state["trade_id"]
        direction = state["direction"]
        entry = state["entry_price"]
        sl = state["stop_loss"]
        tp1 = state["take_profit_1"]
        tp2 = state["take_profit_2"]
        is_simulated = state["is_simulated"]

        # Retrieve current price from WebSocket memory cache with REST fallback
        from app.services.data_loader import data_loader
        current_price = data_loader.memory_manager.get_latest_price(symbol)
        
        if current_price is None or current_price == 0.0:
            try:
                ticker = await exchange_service.fetch_ticker(symbol)
                current_price = float(
                    ticker.get("close") or ticker.get("last") or entry
                )
            except Exception as exc:
                logger.warning(
                    f"Could not fetch fallback live price for {symbol}: {exc}. Skipping check."
                )
                return

        opp_side = "sell" if direction == "LONG" else "buy"
        r_client = await self.get_redis()

        tp1_hit = (direction == "LONG" and current_price >= tp1) or (
            direction == "SHORT" and current_price <= tp1
        )
        tp2_hit = (direction == "LONG" and current_price >= tp2) or (
            direction == "SHORT" and current_price <= tp2
        )
        sl_hit = (direction == "LONG" and current_price <= sl) or (
            direction == "SHORT" and current_price >= sl
        )

        # --- A. TP1 hit → move SL to break-even ---
        if tp1_hit and not state["tp1_filled"]:
            logger.info(
                f"TP1 hit for {symbol} @ {current_price}. "
                f"Moving Stop Loss to Break-Even ({entry})"
            )
            state["tp1_filled"] = True
            success = True

            if not is_simulated:
                try:
                    old_sl_id = (
                        state["order_details"].get("sl_order_id")
                        or state.get("sl_order_id")
                    )
                    if old_sl_id:
                        await exchange_service.exchange.cancel_order(
                            old_sl_id, symbol
                        )
                    new_sl_order = await exchange_service.create_stop_market_order(
                        symbol, opp_side, state["quantity"], entry
                    )
                    new_sl_id = new_sl_order.get("id")
                    state["order_details"]["sl_order_id"] = new_sl_id
                    state["sl_order_id"] = new_sl_id
                except Exception as exc:
                    logger.error(
                        f"Failed to adjust SL order on exchange for {symbol}: {exc}"
                    )
                    success = False
            else:
                new_sl_id = f"mock_sl_be_{trade_id[:8]}"
                state["order_details"]["sl_order_id"] = new_sl_id
                state["sl_order_id"] = new_sl_id

            if success:
                state["stop_loss"] = entry
                state["sl_at_be"] = True
                state["smart_trailing_active"] = True  # Enable smart trailing stop-loss
                await supabase_manager.log_trade_event(
                    trade_id, "PARTIAL_TP1_FILLED", tp1, {"new_sl": entry}
                )
                await supabase_manager.log_trade_event(
                    trade_id, "SL_UPDATED_TO_BE", entry
                )
                await supabase_manager.update_trade_record(
                    trade_id, {"stop_loss": entry}
                )
                await r_client.set(redis_key, json.dumps(state))
                logger.info(
                    f"Successfully adjusted SL to BE, activated smart trailing, and synced state to Redis for {symbol}"
                )

        # --- B. TP2 hit → full close (win) ---
        elif tp2_hit:
            logger.info(
                f"TP2 hit for {symbol} @ {current_price}. Closing position with success."
            )
            if not is_simulated:
                await exchange_service.cancel_orders_for_symbol(symbol)

            pnl_pct = (tp2 - entry) / entry
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            pnl_usdt = pnl_pct * entry * state["quantity"]

            await supabase_manager.update_trade_record(
                trade_id,
                {
                    "status": "CLOSED",
                    "closed_at": datetime.now(timezone.utc),
                    "close_reason": "TAKE_PROFIT_2",
                    "final_pnl": round(pnl_usdt, 4),
                },
            )
            await supabase_manager.log_trade_event(
                trade_id,
                "FULL_CLOSE",
                tp2,
                {"reason": "TAKE_PROFIT_2", "pnl": pnl_usdt},
            )
            await r_client.delete(redis_key)

        # --- C. SL hit → full close (loss) ---
        elif sl_hit:
            logger.info(
                f"SL hit for {symbol} @ {current_price}. Closing position with loss."
            )
            if not is_simulated:
                await exchange_service.cancel_orders_for_symbol(symbol)

            pnl_pct = (sl - entry) / entry
            if direction == "SHORT":
                pnl_pct = -pnl_pct
            pnl_usdt = pnl_pct * entry * state["quantity"]

            await supabase_manager.update_trade_record(
                trade_id,
                {
                    "status": "CLOSED",
                    "closed_at": datetime.now(timezone.utc),
                    "close_reason": "STOP_LOSS",
                    "final_pnl": round(pnl_usdt, 4),
                },
            )
            await supabase_manager.log_trade_event(
                trade_id,
                "FULL_CLOSE",
                sl,
                {"reason": "STOP_LOSS", "pnl": pnl_usdt},
            )
            await r_client.delete(redis_key)

        # --- D. Smart Trailing Stop-Loss (Runs only when TP1 is filled, trailing is active, and trade is still active) ---
        if state.get("tp1_filled") and state.get("smart_trailing_active") and not tp2_hit and not sl_hit:
            current_time = datetime.now(timezone.utc).timestamp()
            last_check = self._last_trail_check.get(trade_id, 0.0) if hasattr(self, "_last_trail_check") else 0.0
            
            # Check at most once every 15 seconds to avoid API limit issues
            if current_time - last_check >= 15.0:
                if not hasattr(self, "_last_trail_check"):
                    self._last_trail_check = {}
                self._last_trail_check[trade_id] = current_time
                
                try:
                    from app.services.market_data_service import get_multi_timeframe_ohlcv
                    from app.services.core_math_engine import detect_swings
                    import pandas as pd
                    
                    # Fetch 1m candles for granular localized swings
                    tf_data = await get_multi_timeframe_ohlcv(symbol, ["1m"], limit=50)
                    candles_1m = tf_data.get("1m", [])
                    
                    if candles_1m:
                        df = pd.DataFrame([c.model_dump() for c in candles_1m])
                        swings = detect_swings(df, n=3)
                        
                        candidate_sl = None
                        if direction == "LONG":
                            # Look for the highest confirmed Swing Low that:
                            # 1. Moves Stop Loss higher than the current Stop Loss (sl)
                            # 2. Remains below the current live price
                            valid_swings = []
                            for sw in swings:
                                if sw["type"] == "LOW":
                                    buffered_price = sw["price"] * (1 - settings.STOP_LOSS_BUFFER_PCT)
                                    if buffered_price > sl and buffered_price < current_price:
                                        valid_swings.append(buffered_price)
                            if valid_swings:
                                candidate_sl = max(valid_swings)
                        else:  # SHORT
                            # Look for the lowest confirmed Swing High that:
                            # 1. Moves Stop Loss lower than the current Stop Loss (sl)
                            # 2. Remains above the current live price
                            valid_swings = []
                            for sw in swings:
                                if sw["type"] == "HIGH":
                                    buffered_price = sw["price"] * (1 + settings.STOP_LOSS_BUFFER_PCT)
                                    if buffered_price < sl and buffered_price > current_price:
                                        valid_swings.append(buffered_price)
                            if valid_swings:
                                candidate_sl = min(valid_swings)
                                
                        if candidate_sl is not None:
                            candidate_sl = round(candidate_sl, 6)
                            logger.info(
                                f"Smart Trailing: New confirmed localized swing found for {symbol}. "
                                f"Trailing SL from {sl} to {candidate_sl}"
                            )
                            
                            success = True
                            if not is_simulated:
                                try:
                                    old_sl_id = (
                                        state["order_details"].get("sl_order_id")
                                        or state.get("sl_order_id")
                                    )
                                    if old_sl_id:
                                        await exchange_service.exchange.cancel_order(
                                            old_sl_id, symbol
                                        )
                                    new_sl_order = await exchange_service.create_stop_market_order(
                                        symbol, opp_side, state["quantity"], candidate_sl
                                    )
                                    new_sl_id = new_sl_order.get("id")
                                    state["order_details"]["sl_order_id"] = new_sl_id
                                    state["sl_order_id"] = new_sl_id
                                except Exception as exc:
                                    logger.error(
                                        f"Failed to adjust trailed SL order on exchange for {symbol}: {exc}"
                                    )
                                    success = False
                            else:
                                new_sl_id = f"mock_sl_trail_{trade_id[:8]}"
                                state["order_details"]["sl_order_id"] = new_sl_id
                                state["sl_order_id"] = new_sl_id
                                
                            if success:
                                state["stop_loss"] = candidate_sl
                                await supabase_manager.log_trade_event(
                                    trade_id, "SL_TRAILED", candidate_sl, {"old_sl": sl}
                                )
                                await supabase_manager.update_trade_record(
                                    trade_id, {"stop_loss": candidate_sl}
                                )
                                await r_client.set(redis_key, json.dumps(state))
                                logger.info(
                                    f"Successfully trailed SL to {candidate_sl} and synced state to Redis for {symbol}"
                                )
                except Exception as trailing_err:
                    logger.error(f"Error in smart trailing evaluation for {symbol}: {trailing_err}")


trade_manager = TradeManager()
