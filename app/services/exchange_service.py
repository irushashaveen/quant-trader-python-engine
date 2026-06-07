import ccxt.async_support as ccxt
from app.core.config import settings
from app.core.logging import logger
from typing import List, Any, Dict, Optional

class ExchangeService:
    def __init__(self):
        self.exchange = None

    async def initialize(self):
        if self.exchange is None:
            config = {
                'enableRateLimit': True,
                'timeout': 10000,
            }
            if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
                config['apiKey'] = settings.BINANCE_API_KEY
                config['secret'] = settings.BINANCE_API_SECRET
            
            # Using binanceusdm for USD-M Futures contract trading
            self.exchange = ccxt.binanceusdm(config)
            
            if settings.BINANCE_USE_TESTNET:
                self.exchange.set_sandbox_mode(True)
                
            # Verify Hedge Mode vs One-Way Mode
            if settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET:
                try:
                    res = await self.exchange.fapiPrivateGetPositionSideDual()
                    if res and str(res.get('dualSidePosition')).lower() == 'true':
                        msg = "CRITICAL ERROR: Hedge Mode is ACTIVE on this Binance account. The engine's order logic requires One-Way Mode. Please switch to One-Way Mode in Binance settings before trading."
                        logger.error(msg)
                        raise RuntimeError(msg)
                    else:
                        logger.info("Verified: Binance account is in One-Way position mode.")
                except Exception as e:
                    if isinstance(e, RuntimeError):
                        raise e
                    logger.warning(f"Could not verify Hedge/One-Way position mode: {e}")
                    
            logger.info("Exchange service initialized successfully (Binance USD-M Futures).")

    async def close(self):
        if self.exchange is not None:
            await self.exchange.close()
            self.exchange = None
            logger.info("Exchange service connection closed.")

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[List[Any]]:
        await self.initialize()
        try:
            logger.info(f"Fetching OHLCV for {symbol} on {timeframe} (limit: {limit})")
            # Futures contracts on Binance are formatted as BTC/USDT or BTC/USDT:USDT depending on version.
            # ccxt.binanceusdm supports standard symbol "BTC/USDT" mapping to BTCUSDT futures contract.
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return ohlcv
        except Exception as e:
            logger.error(f"Error fetching OHLCV from Binance for {symbol} on {timeframe}: {str(e)}")
            raise e

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        await self.initialize()
        try:
            return await self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Error fetching ticker from Binance for {symbol}: {str(e)}")
            raise e

    async def create_market_order(self, symbol: str, side: str, amount: float) -> Dict[str, Any]:
        """
        Submits a market entry order (buy/sell).
        """
        await self.initialize()
        try:
            logger.info(f"Placing Futures MARKET order: {side} {amount} {symbol}")
            order = await self.exchange.create_market_order(symbol, side, amount)
            logger.info(f"Futures MARKET order filled: {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Error placing futures market order for {symbol}: {str(e)}")
            raise e

    async def create_limit_order(self, symbol: str, side: str, amount: float, price: float, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Submits a limit order (e.g. for take profits).
        """
        await self.initialize()
        try:
            logger.info(f"Placing Futures LIMIT order: {side} {amount} {symbol} @ {price}")
            # reduceOnly helps ensure it only closes existing positions
            default_params = {'reduceOnly': True}
            if params:
                default_params.update(params)
            
            order = await self.exchange.create_limit_order(symbol, side, amount, price, default_params)
            logger.info(f"Futures LIMIT order placed: {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Error placing futures limit order for {symbol}: {str(e)}")
            raise e

    async def create_stop_market_order(self, symbol: str, side: str, amount: float, stop_price: float, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Submits a STOP_MARKET stop loss order.
        """
        await self.initialize()
        try:
            logger.info(f"Placing Futures STOP_MARKET order: {side} {amount} {symbol} trigger={stop_price}")
            default_params = {
                'stopPrice': stop_price,
                'reduceOnly': True
            }
            if params:
                default_params.update(params)
            
            order = await self.exchange.create_order(symbol, 'STOP_MARKET', side, amount, None, default_params)
            logger.info(f"Futures STOP_MARKET order placed: {order.get('id')}")
            return order
        except Exception as e:
            logger.error(f"Error placing futures stop market order for {symbol}: {str(e)}")
            raise e

    async def cancel_orders_for_symbol(self, symbol: str) -> bool:
        """
        Cancels all open orders for a specific trading pair.
        """
        await self.initialize()
        try:
            logger.info(f"Cancelling all open orders for {symbol}")
            await self.exchange.cancel_all_orders(symbol)
            return True
        except Exception as e:
            logger.error(f"Error cancelling orders for {symbol}: {str(e)}")
            return False

    async def fetch_position_details(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetches position information for a specific trading pair.
        """
        await self.initialize()
        try:
            positions = await self.exchange.fetch_positions(symbols=[symbol])
            if positions and len(positions) > 0:
                # Return the position dict for the exact symbol
                for pos in positions:
                    if pos.get('symbol') == symbol:
                        return pos
            return None
        except Exception as e:
            logger.error(f"Error fetching position details for {symbol}: {str(e)}")
            return None

exchange_service = ExchangeService()
