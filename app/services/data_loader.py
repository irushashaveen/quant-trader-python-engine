import asyncio
from collections import deque
from datetime import datetime, timezone
import ccxt.pro as ccxt
from typing import Dict, Any, List, Optional

from app.core.config import settings
from app.core.logging import logger

class MemoryManager:
    def __init__(self, maxlen: int = 10000):
        """
        Stores live tick data using Python's highly optimized double-ended queue.
        Oldest trades are automatically dropped to prevent RAM overflow.
        All data structures are indexed by symbol to support multi-symbol tracking.
        """
        self.maxlen = maxlen
        self.trades: Dict[str, deque] = {}         # symbol -> deque of trades
        self.order_books: Dict[str, Dict] = {}     # symbol -> lightweight order book dict
        self.liquidations: Dict[str, deque] = {}   # symbol -> deque of liquidations
        self.cvds: Dict[str, float] = {}           # symbol -> cumulative volume delta
        self.latest_prices: Dict[str, float] = {}  # symbol -> latest trade price
        self.update_event = asyncio.Event()

    def _ensure_symbol(self, symbol: str):
        """Ensure dictionaries are initialized for the given symbol."""
        if symbol not in self.trades:
            self.trades[symbol] = deque(maxlen=self.maxlen)
        if symbol not in self.order_books:
            self.order_books[symbol] = {}
        if symbol not in self.liquidations:
            self.liquidations[symbol] = deque(maxlen=1000)
        if symbol not in self.cvds:
            self.cvds[symbol] = 0.0
        if symbol not in self.latest_prices:
            self.latest_prices[symbol] = 0.0

    def add_trade(self, symbol: str, trade: Dict[str, Any]):
        """
        Processes a single live trade. Calculates Cumulative Volume Delta (CVD) in O(1).
        Maker Buy (Taker Sell) adds volume, Maker Sell (Taker Buy) subtracts volume.
        """
        self._ensure_symbol(symbol)
        
        amount = float(trade.get('amount', 0.0))
        side = trade.get('side', 'buy')
        taker_or_maker = trade.get('takerOrMaker', 'taker')
        
        # Maker buy means taker sold (side == 'sell'), maker sell means taker bought (side == 'buy')
        # If takerOrMaker is maker, side is maker's side
        is_buy = (side == 'buy' if taker_or_maker == 'maker' else side == 'sell')
        delta = amount if is_buy else -amount
        
        # O(1) Running CVD: If we are at capacity, subtract the oldest trade's delta
        if len(self.trades[symbol]) >= self.trades[symbol].maxlen:
            old_trade = self.trades[symbol][0]
            old_amount = float(old_trade.get('amount', 0.0))
            old_side = old_trade.get('side', 'buy')
            old_tom = old_trade.get('takerOrMaker', 'taker')
            old_is_buy = (old_side == 'buy' if old_tom == 'maker' else old_side == 'sell')
            old_delta = old_amount if old_is_buy else -old_amount
            self.cvds[symbol] -= old_delta
            
        self.trades[symbol].append(trade)
        self.cvds[symbol] += delta
        self.latest_prices[symbol] = float(trade.get('price', 0.0))
        
        # Signal event-driven listeners of a new update
        self.update_event.set()
        self.update_event = asyncio.Event()

    def get_cvd(self, symbol: str) -> float:
        self._ensure_symbol(symbol)
        return self.cvds[symbol]

    def get_normalized_cvd(self, symbol: str) -> float:
        """
        Returns CVD normalized against the sum of volumes of all active trades in deque.
        Values range between -1.0 (all maker sells) and +1.0 (all maker buys).
        """
        self._ensure_symbol(symbol)
        total_vol = sum(float(t.get('amount', 0.0)) for t in self.trades[symbol])
        if total_vol == 0:
            return 0.0
        return self.cvds[symbol] / total_vol

    def get_latest_price(self, symbol: str) -> Optional[float]:
        self._ensure_symbol(symbol)
        return self.latest_prices.get(symbol)

    def update_order_book(self, symbol: str, orderbook: Dict[str, Any]):
        """Saves a lightweight snapshot of the top 20 bids/asks to conserve RAM."""
        self._ensure_symbol(symbol)
        self.order_books[symbol] = {
            'bids': orderbook.get('bids', [])[:20],
            'asks': orderbook.get('asks', [])[:20],
            'timestamp': orderbook.get('timestamp')
        }

    def get_order_book_imbalance(self, symbol: str, depth: int = 10) -> float:
        """
        Calculates bid/ask volume imbalance ratio: Bid Vol / (Bid Vol + Ask Vol)
        using the top `depth` levels of the order book.
        Returns 0.5 (balanced) if no order book is loaded.
        """
        self._ensure_symbol(symbol)
        ob = self.order_books.get(symbol)
        if not ob or 'bids' not in ob or 'asks' not in ob:
            return 0.5
            
        bids = ob['bids'][:depth]
        asks = ob['asks'][:depth]
        
        bid_vol = sum(float(level[1]) for level in bids)
        ask_vol = sum(float(level[1]) for level in asks)
        
        total_vol = bid_vol + ask_vol
        if total_vol == 0.0:
            return 0.5
            
        return bid_vol / total_vol

    def add_liquidation(self, symbol: str, liq: Dict[str, Any]):
        self._ensure_symbol(symbol)
        self.liquidations[symbol].append(liq)

    def get_recent_liquidations_stats(self, symbol: str, lookback_seconds: int = 60) -> Dict[str, Any]:
        """
        Tracks number and volume of liquidations within the last N seconds.
        Used to detect liquidation sweeps.
        """
        self._ensure_symbol(symbol)
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        cutoff_ms = now_ms - (lookback_seconds * 1000)
        
        recent_liqs = [
            liq for liq in self.liquidations[symbol]
            if liq.get('timestamp', 0) >= cutoff_ms
        ]
        
        total_qty = sum(float(liq.get('amount', 0.0)) for liq in recent_liqs)
        total_vol = sum(float(liq.get('amount', 0.0)) * float(liq.get('price', 0.0)) for liq in recent_liqs)
        
        return {
            "count": len(recent_liqs),
            "total_qty": total_qty,
            "total_vol": total_vol
        }

    async def wait_for_update(self):
        """Wait until a new trade update occurs."""
        await self.update_event.wait()

class DataLoader:
    def __init__(self):
        self.exchange = None
        self.memory_manager = MemoryManager()
        self.running = False
        self.tasks = []

    async def initialize(self):
        if self.exchange is None:
            config = {
                'enableRateLimit': True,
                'newUpdates': True, # Required by ccxt.pro to return only delta updates
                'timeout': 10000,
            }
            if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
                config['apiKey'] = settings.BINANCE_API_KEY
                config['secret'] = settings.BINANCE_API_SECRET
            
            # Use Binance USD-M futures with ccxt.pro for websockets
            self.exchange = ccxt.binanceusdm(config)
            
            if settings.BINANCE_USE_TESTNET:
                self.exchange.set_sandbox_mode(True)
                
            logger.info("DataLoader initialized for high-frequency WebSockets.")

    async def start_streams(self, symbols: List[str]):
        """
        Initializes the firehose. Subscribes to trades, orderbook, and liquidations.
        """
        await self.initialize()
        self.running = True
        
        for symbol in symbols:
            self.tasks.append(asyncio.create_task(self._watch_trades_loop(symbol)))
            self.tasks.append(asyncio.create_task(self._watch_order_book_loop(symbol)))
            
            # Binance supports liquidations via ws, CCXT wraps it in watch_liquidations or watch_trades logic
            if hasattr(self.exchange, 'watch_liquidations'):
                 self.tasks.append(asyncio.create_task(self._watch_liquidations_loop(symbol)))

        logger.info(f"Started WebSocket streams (Tape, L2, Liquidations) for {symbols}")

    async def stop_streams(self):
        self.running = False
        for task in self.tasks:
            task.cancel()
        if self.exchange:
            await self.exchange.close()
        logger.info("WebSocket streams stopped.")

    async def _watch_trades_loop(self, symbol: str):
        while self.running:
            try:
                # The Tape: Receives trades in real-time
                trades = await self.exchange.watch_trades(symbol)
                for trade in trades:
                    self.memory_manager.add_trade(symbol, trade)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error watching trades for {symbol}: {e}")
                await asyncio.sleep(1)

    async def _watch_order_book_loop(self, symbol: str):
        while self.running:
            try:
                # L2 Deltas: Spreads, spoofing detection
                orderbook = await self.exchange.watch_order_book(symbol)
                self.memory_manager.update_order_book(symbol, orderbook)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error watching order book for {symbol}: {e}")
                await asyncio.sleep(1)

    async def _watch_liquidations_loop(self, symbol: str):
        import inspect
        while self.running:
            try:
                # Institutional sweep detector
                liquidations = await self.exchange.watch_liquidations(symbol)
                
                # Check for CCXT Pro double-nested coroutine bug
                while inspect.iscoroutine(liquidations):
                    liquidations = await liquidations
                    
                if not isinstance(liquidations, list):
                    liquidations = [liquidations]
                for liq in liquidations:
                    self.memory_manager.add_liquidation(symbol, liq)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error watching liquidations for {symbol}: {e}")
                await asyncio.sleep(5)

data_loader = DataLoader()
