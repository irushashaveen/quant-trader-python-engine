from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
import collections
import ccxt.pro as ccxt
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

class WSMemoryManager:
    def __init__(self, maxlen=10000):
        self.trades = collections.deque(maxlen=maxlen)
        self.order_book = {"bids": [], "asks": []}
        self.ticker = {"price": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0}
        self.cvd = 0.0
        self.sweeps = collections.deque(maxlen=100)

    def update_ticker(self, tick_data):
        self.ticker = {
            "price": float(tick_data.get("close") or tick_data.get("last") or 0.0),
            "high": float(tick_data.get("high") or 0.0),
            "low": float(tick_data.get("low") or 0.0),
            "volume": float(tick_data.get("baseVolume") or tick_data.get("volume") or 0.0)
        }

    def add_trade(self, trade):
        self.trades.append(trade)
        
        # Calculate CVD
        side = trade.get("side", "buy")
        amount = float(trade.get("amount", 0.0))
        price = float(trade.get("price", 0.0))
        notional = amount * price
        
        if side == "buy":
            self.cvd += amount
        else:
            self.cvd -= amount
            
        # Detect simulated Liquidity Sweep
        # Threshold: Notional size >= $30,000 USDT for trade sweeps
        sweep_threshold = 30000.0
        if notional >= sweep_threshold:
            sweep_event = {
                "timestamp": trade.get("timestamp") or int(asyncio.get_event_loop().time() * 1000),
                "side": side.upper(),
                "size": amount,
                "price": price,
                "notional": notional
            }
            self.sweeps.append(sweep_event)

    def update_order_book(self, ob):
        bids = ob.get("bids", [])[:15]
        asks = ob.get("asks", [])[:15]
        self.order_book = {
            "bids": [[float(b[0]), float(b[1])] for b in bids],
            "asks": [[float(a[0]), float(a[1])] for a in asks]
        }

    def get_aggregated_payload(self):
        recent_trades = []
        for t in list(self.trades)[-20:]:
            recent_trades.append({
                "timestamp": t.get("timestamp"),
                "side": t.get("side", "buy").upper(),
                "price": float(t.get("price", 0.0)),
                "amount": float(t.get("amount", 0.0))
            })
            
        return {
            "ticker": self.ticker,
            "order_book": self.order_book,
            "trades": recent_trades,
            "cvd": round(self.cvd, 4),
            "sweeps": list(self.sweeps)[-5:]
        }

async def watch_ticker_loop(exchange, symbol, mem_manager, state_flag):
    while state_flag["running"]:
        try:
            ticker = await exchange.watch_ticker(symbol)
            mem_manager.update_ticker(ticker)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WS Ticker loop error for {symbol}: {e}")
            await asyncio.sleep(2)

async def watch_trades_loop(exchange, symbol, mem_manager, state_flag):
    while state_flag["running"]:
        try:
            trades = await exchange.watch_trades(symbol)
            if not isinstance(trades, list):
                trades = [trades]
            for trade in trades:
                mem_manager.add_trade(trade)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WS Trades loop error for {symbol}: {e}")
            await asyncio.sleep(2)

async def watch_orderbook_loop(exchange, symbol, mem_manager, state_flag):
    while state_flag["running"]:
        try:
            ob = await exchange.watch_order_book(symbol)
            mem_manager.update_order_book(ob)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WS Orderbook loop error for {symbol}: {e}")
            await asyncio.sleep(2)

async def broadcast_loop(websocket, mem_manager, state_flag):
    while state_flag["running"]:
        try:
            payload = mem_manager.get_aggregated_payload()
            await websocket.send_json(payload)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"WS Broadcast error: {e}")
            break
        await asyncio.sleep(0.15)  # Broadcast every 150ms

@router.websocket("/ws/stream/{symbol:path}")
async def websocket_stream(websocket: WebSocket, symbol: str):
    await websocket.accept()
    logger.info(f"Frontend client connected to WebSocket stream for symbol: {symbol}")
    
    config = {
        'enableRateLimit': True,
        'newUpdates': True,
        'timeout': 10000,
    }
    if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
        config['apiKey'] = settings.BINANCE_API_KEY
        config['secret'] = settings.BINANCE_API_SECRET
        
    exchange = ccxt.binanceusdm(config)
    if settings.BINANCE_USE_TESTNET:
        exchange.set_sandbox_mode(True)
        
    mem_manager = WSMemoryManager()
    state_flag = {"running": True}
    
    # Spawn background loops
    ticker_task = asyncio.create_task(watch_ticker_loop(exchange, symbol, mem_manager, state_flag))
    trades_task = asyncio.create_task(watch_trades_loop(exchange, symbol, mem_manager, state_flag))
    ob_task = asyncio.create_task(watch_orderbook_loop(exchange, symbol, mem_manager, state_flag))
    broadcast_task = asyncio.create_task(broadcast_loop(websocket, mem_manager, state_flag))
    
    try:
        while True:
            # Maintain active connection and listen for any client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"Frontend client disconnected from WebSocket stream for {symbol}")
    except Exception as e:
        logger.error(f"WebSocket connection error for {symbol}: {e}")
    finally:
        state_flag["running"] = False
        
        # Cancel tasks
        for task in [ticker_task, trades_task, ob_task, broadcast_task]:
            task.cancel()
            
        try:
            await exchange.close()
            logger.info(f"CCXT Pro WS client closed successfully for {symbol}")
        except Exception as close_err:
            logger.error(f"Error closing exchange in WS handler: {close_err}")
