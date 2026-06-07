import uuid
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from supabase import create_client, Client
from app.core.config import settings
from app.core.logging import logger

class SupabaseManager:
    def __init__(self):
        self.client = None

    def initialize(self) -> bool:
        if self.client is None:
            url = settings.SUPABASE_URL
            key = settings.SUPABASE_KEY
            if not url or not key:
                logger.warning("SUPABASE_URL or SUPABASE_KEY is not set. Supabase operations will be simulated.")
                return False
            try:
                self.client = create_client(url, key)
                logger.info("Supabase client initialized successfully.")
                return True
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
                self.client = None
                return False
        return True

    async def create_trade_record(self, trade_data: Dict[str, Any]) -> str:
        """
        Inserts a new trade record. Returns the trade UUID string.
        """
        self.initialize()
        trade_id = trade_data.get("id") or str(uuid.uuid4())
        
        # Clone to avoid mutating the original dict
        data = trade_data.copy()
        data["id"] = trade_id
        
        # Format timestamps to ISO strings
        for field in ["executed_at", "closed_at"]:
            if field in data and isinstance(data[field], datetime):
                data[field] = data[field].isoformat()

        if self.client is None:
            logger.info(f"[SIMULATED SUPABASE] Create trade: {data}")
            return trade_id

        try:
            # supabase.table("trades").insert(data).execute() is blocking (synchronous library)
            # Run in executor or execute synchronously since it is fast
            await asyncio.to_thread(lambda: self.client.table("trades").insert(data).execute())
            logger.info(f"Supabase trade record created: {trade_id}")
            return trade_id
        except Exception as e:
            # Check if it is a missing column error for the 'mode' field, and retry without it
            if "column" in str(e).lower() and "mode" in str(e).lower():
                logger.warning("Supabase trades table appears to miss the 'mode' column. Retrying insert without it.")
                try:
                    fallback_data = data.copy()
                    fallback_data.pop("mode", None)
                    await asyncio.to_thread(lambda: self.client.table("trades").insert(fallback_data).execute())
                    logger.info(f"Supabase trade record created (fallback): {trade_id}")
                    return trade_id
                except Exception as fallback_err:
                    logger.error(f"Fallback insert failed: {fallback_err}")
            logger.error(f"Failed to insert trade to Supabase: {e}. Falling back to simulated mode.")
            return trade_id

    async def update_trade_record(self, trade_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Updates an existing trade record.
        """
        self.initialize()
        # Clone to avoid mutating the original dict
        data = update_data.copy()
        for field in ["executed_at", "closed_at"]:
            if field in data and isinstance(data[field], datetime):
                data[field] = data[field].isoformat()

        if self.client is None:
            logger.info(f"[SIMULATED SUPABASE] Update trade {trade_id}: {data}")
            return True

        try:
            await asyncio.to_thread(lambda: self.client.table("trades").update(data).eq("id", trade_id).execute())
            logger.info(f"Supabase trade record updated: {trade_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update trade in Supabase: {e}")
            return False

    async def log_trade_event(self, trade_id: str, event_type: str, value: float, details: Optional[Dict[str, Any]] = None) -> bool:
        """
        Logs a trade event.
        """
        self.initialize()
        event_id = str(uuid.uuid4())
        event_data = {
            "id": event_id,
            "trade_id": trade_id,
            "event_type": event_type,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {}
        }

        if self.client is None:
            logger.info(f"[SIMULATED SUPABASE] Log trade event: {event_data}")
            return True

        try:
            await asyncio.to_thread(lambda: self.client.table("trade_events").insert(event_data).execute())
            logger.info(f"Supabase trade event logged: {event_type} (value: {value}) for trade {trade_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to log trade event to Supabase: {e}")
            return False

supabase_manager = SupabaseManager()
