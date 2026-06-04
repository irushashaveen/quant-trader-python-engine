from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api import routes_health
from app.api.routes_signal import router as signal_router
from app.api.routes_market import router as market_router
from app.api.routes_analysis import router as analysis_router
from app.api.routes_decision import router as decision_router
from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Shutdown: Close CCXT exchange connection
    from app.services.exchange_service import exchange_service
    await exchange_service.close()

app = FastAPI(
    title="Quant Trader Python Engine",
    description="Engine for execution, market data, strategy logic, and trade management.",
    version="0.1.0",
    lifespan=lifespan
)

# Include routers
app.include_router(routes_health.router, tags=["Health"])
app.include_router(signal_router, prefix="/api/v1", tags=["Signal"])
app.include_router(market_router, prefix="/api/v1/market", tags=["Market"])
app.include_router(analysis_router, prefix="/api/v1/analysis", tags=["Analysis"])
app.include_router(decision_router, prefix="/api/v1/decision", tags=["Decision"])

@app.get("/")
def read_root():
    return {
        "message": "Welcome to the Quant Trader Python Engine API",
        "docs_url": "/docs"
    }

