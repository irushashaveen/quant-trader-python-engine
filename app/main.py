import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes_health, routes_ws
from app.api.routes_signal import router as signal_router
from app.api.routes_market import router as market_router
from app.api.routes_analysis import router as analysis_router
from app.api.routes_decision import router as decision_router
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize services and start WebSocket streams
    from app.services.exchange_service import exchange_service
    from app.services.data_loader import data_loader
    from app.services.trade_manager import trade_manager
    from app.services.market_data_service import get_multi_timeframe_ohlcv, fetch_order_flow_data
    from app.services.smc_engine import run_smc_analysis
    from app.services.order_flow_engine import evaluate_order_flow
    from app.core.logging import logger
    
    # 1. Initialize CCXT exchange client
    await exchange_service.initialize()
    
    # 2. Start WebSocket data streams in the background
    default_symbols = ["BTC/USDT"]
    await data_loader.start_streams(default_symbols)
    
    # 3. Start background trade monitoring task
    await trade_manager.start_monitoring()
    
    # 4. Dry-run orchestration check as a background task to avoid blocking startup
    async def run_dry_run():
        try:
            logger.info("Lifespan: Running initial orchestration dry-run check...")
            timeframe_data = await get_multi_timeframe_ohlcv("BTC/USDT", ["1m", "5m", "15m", "1h", "4h"], 100)
            smc_res = await run_smc_analysis("BTC/USDT", timeframe_data)
            logger.info(f"SMC dry-run successful. Bias: {smc_res.aggregate_bias_score}, Confidence: {smc_res.confidence}")
            
            of_data = await fetch_order_flow_data("BTC/USDT")
            of_res = evaluate_order_flow(of_data)
            logger.info(f"Order Flow dry-run successful. Action: {of_res.action}, Reason: {of_res.reason}")
            logger.info("Lifespan: Initial orchestration dry-run check completed successfully.")
        except Exception as e:
            logger.warning(f"Lifespan: Startup dry-run check skipped/failed (sandbox or connection limits): {e}")

    asyncio.create_task(run_dry_run())
    
    yield
    
    # Shutdown: Stop background tasks and close CCXT connection
    await trade_manager.stop_monitoring()
    await data_loader.stop_streams()
    await exchange_service.close()

app = FastAPI(

    title="Quant Trader Python Engine",
    description="Engine for execution, market data, strategy logic, and trade management.",
    version="0.1.0",
    lifespan=lifespan
)

# CORS — allow browser clients (Next.js dev server + Docker) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(routes_health.router, tags=["Health"])
app.include_router(signal_router, prefix="/api/v1", tags=["Signal"])
app.include_router(market_router, prefix="/api/v1/market", tags=["Market"])
app.include_router(analysis_router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(decision_router, prefix="/api/v1/decision", tags=["Decision"])
app.include_router(routes_ws.router, prefix="/api/v1", tags=["WebSocket"])

@app.get("/")
def read_root():
    return {
        "message": "Welcome to the Quant Trader Python Engine API",
        "docs_url": "/docs"
    }

