import asyncio
import pandas as pd
from typing import Dict, List
from app.services.exchange_service import exchange_service
from app.schemas.market import Candle
from app.core.logging import logger

async def fetch_cleaned_ohlcv(symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
    # Fetch limit + 1 to account for dropping the current active candle
    raw_ohlcv = await exchange_service.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
    
    if not raw_ohlcv:
        return []

    # Convert to DataFrame
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df = pd.DataFrame(raw_ohlcv, columns=columns)
    
    # Drop the last row (currently active/forming candle) to prevent lookahead bias
    if len(df) > 0:
        df = df.iloc[:-1]
    
    # Parse timestamp to UTC datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    
    # Format and convert to list of Candle schemas
    candles = []
    for _, row in df.iterrows():
        candles.append(
            Candle(
                timestamp=row["timestamp"].to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"])
            )
        )
    # Ensure it returns exactly the requested limit (or less if history is short)
    return candles[-limit:]

async def get_multi_timeframe_ohlcv(
    symbol: str, 
    timeframes: List[str], 
    limit: int = 100
) -> Dict[str, List[Candle]]:
    # Create concurrent async tasks for each timeframe
    tasks = {tf: fetch_cleaned_ohlcv(symbol, tf, limit) for tf in timeframes}
    
    # Execute concurrently
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    
    market_data = {}
    for tf, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            logger.error(f"Failed to process timeframe {tf} for {symbol}: {str(result)}")
            raise result
        market_data[tf] = result
        
    return market_data
