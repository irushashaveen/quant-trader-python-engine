import asyncio
from collections import deque
import ccxt.pro as ccxt
from typing import Dict, Any, List

from app.core.config import settings
from app.core.logging import logger

class MemoryManager:
    def __init__(self, maxlen: int = 10000):
        """
        Stores live tick data using Python's highly optimized double-ended queue.
        Oldest trades are automatically dropped to prevent RAM overflow.
        """
        self.trades_deque = deque(maxlen=maxlen)
        self.order_book = {} 
        self.liquidations = deque(maxlen=1000)
        self.cvd = 0.0

    def add_trade(self, trade: Dict[str, Any]):
        """
        Processes a single live trade. Calculates Cumulative Volume Delta (CVD).
        """
        self.trades_deque.append(trade)
        
        # Calculate dynamic CVD
        side = trade.get('side', 'buy')
        amount = float(trade.get('amount', 0.0))
        if side == 'buy':
            self.cvd += amount
        elif side == 'sell':
            self.cvd -= amount

    def get_cvd(self) -> float:
        return self.cvd

    def update_order_book(self, orderbook: Dict[str, Any]):
        self.order_book = orderbook

    def add_liquidation(self, liq: Dict[str, Any]):
        self.liquidations.append(liq)

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
                    self.memory_manager.add_trade(trade)
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
                self.memory_manager.update_order_book(orderbook)
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
                    self.memory_manager.add_liquidation(liq)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error watching liquidations for {symbol}: {e}")
                await asyncio.sleep(5)

data_loader = DataLoader()
