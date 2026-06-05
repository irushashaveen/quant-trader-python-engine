import asyncio
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.logging import logger

class MongoManager:
    def __init__(self):
        self.client = None
        self.db = None

    def initialize(self):
        if self.client is None:
            uri = settings.MONGODB_URI
            if not uri:
                logger.warning("MONGODB_URI is not set. MongoDB operations will be simulated.")
                return False
            try:
                self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
                # Next.js uses quant_db as default database based on connection string
                self.db = self.client.get_database("quant_db")
                logger.info("MongoDB client initialized successfully.")
                return True
            except Exception as e:
                logger.error(f"Failed to initialize MongoDB client: {e}")
                self.client = None
                self.db = None
                return False
        return True

    async def save_snapshot(self, trade_id: str, symbol: str, snapshot_type: str, data: dict) -> bool:
        self.initialize()
        if self.db is None:
            logger.info(f"[SIMULATED MONGO] Save snapshot of type '{snapshot_type}' for {symbol} linked to trade {trade_id}")
            return True
        try:
            doc = {
                "trade_id": trade_id,
                "symbol": symbol,
                "type": snapshot_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc)
            }
            await self.db.snapshots.insert_one(doc)
            logger.info(f"Successfully saved {snapshot_type} snapshot to MongoDB for {symbol} (trade: {trade_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to save snapshot to MongoDB: {e}")
            return False

    async def save_execution_reasoning(self, trade_id: str, reasoning: str) -> bool:
        self.initialize()
        if self.db is None:
            logger.info(f"[SIMULATED MONGO] Save execution reasoning for trade {trade_id}: {reasoning[:60]}...")
            return True
        try:
            doc = {
                "trade_id": trade_id,
                "reasoning": reasoning,
                "timestamp": datetime.now(timezone.utc)
            }
            await self.db.execution_reasoning.insert_one(doc)
            logger.info(f"Successfully saved execution reasoning to MongoDB for trade {trade_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save execution reasoning to MongoDB: {e}")
            return False

mongo_manager = MongoManager()
