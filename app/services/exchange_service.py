import ccxt.async_support as ccxt
from app.core.config import settings
from app.core.logging import logger
from typing import List, Any

class ExchangeService:
    def __init__(self):
        self.exchange = None

    async def initialize(self):
        if self.exchange is None:
            config = {
                'enableRateLimit': True,
            }
            if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
                config['apiKey'] = settings.BINANCE_API_KEY
                config['secret'] = settings.BINANCE_API_SECRET
            
            self.exchange = ccxt.binance(config)
            
            if settings.BINANCE_USE_TESTNET:
                self.exchange.set_sandbox_mode(True)
                
            logger.info("Exchange service initialized successfully (Binance).")

    async def close(self):
        if self.exchange is not None:
            await self.exchange.close()
            self.exchange = None
            logger.info("Exchange service connection closed.")

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[List[Any]]:
        await self.initialize()
        try:
            logger.info(f"Fetching OHLCV for {symbol} on {timeframe} (limit: {limit})")
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return ohlcv
        except Exception as e:
            logger.error(f"Error fetching OHLCV from Binance for {symbol} on {timeframe}: {str(e)}")
            raise e

exchange_service = ExchangeService()
