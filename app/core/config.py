from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    ENV: str = "development"
    PORT: int = 8001
    HOST: str = "0.0.0.0"
    
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_URL: str = "redis://localhost:6379"

    BINANCE_API_KEY: str | None = None
    BINANCE_API_SECRET: str | None = None
    BINANCE_USE_TESTNET: bool = True

    SWING_WINDOW: int = 3
    TIMEFRAME_WEIGHTS: dict[str, float] = {
        "4h": 0.35,
        "1h": 0.30,
        "15m": 0.20,
        "5m": 0.10,
        "1m": 0.05
    }

    # --- Phase 5: Signal Decision Thresholds ---
    # Primary timeframe used for structure/liquidity proximity checks
    PRIMARY_TIMEFRAME: str = "15m"

    # Confidence score gates
    MIN_CONFIDENCE_SCORE: float = 0.35    # Below this → REJECT_LOW_CONFIDENCE
    WAIT_CONFIDENCE_SCORE: float = 0.55   # 0.35–0.55 → WAIT
    AUTO_EXECUTE_CONFIDENCE: float = 0.70 # 0.55–0.70 → requires_manual_confirmation=True, ≥0.70 → full auto

    # Vote confluence: fraction of weighted votes that must align
    VOTE_CONFLUENCE_THRESHOLD: float = 0.60
    # Minimum per-timeframe bias to count as a directional vote
    VOTE_BIAS_THRESHOLD: float = 0.25

    # Risk / reward
    MIN_RISK_REWARD: float = 2.0          # Minimum R:R to TP1 — below this → REJECT_HIGH_RISK
    STOP_LOSS_BUFFER_PCT: float = 0.001   # 0.1% buffer beyond swing/OB level
    TAKE_PROFIT_FIXED_R: float = 2.0      # Fixed R multiple used as TP1 fallback
    TAKE_PROFIT_2_R: float = 3.0          # Fixed R multiple always used for TP2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
