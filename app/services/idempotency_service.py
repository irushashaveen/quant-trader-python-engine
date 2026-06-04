import redis.asyncio as redis
from app.core.config import settings
from app.utils.hashing import generate_signal_hash

async def is_duplicate_signal(symbol: str, direction: str, timestamp: str) -> bool:
    r = await redis.from_url(settings.REDIS_URL)
    signal_hash = generate_signal_hash(symbol, direction, str(timestamp))
    exists = await r.exists(signal_hash)
    if not exists:
        await r.setex(signal_hash, 3600, "1")  # expire after 1 hour
    return bool(exists)
