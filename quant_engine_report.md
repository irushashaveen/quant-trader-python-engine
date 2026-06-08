# Quant Trader Python Engine — In-Depth Technical Report

> **Generated:** 2026-06-07 | **Engine Version:** 0.1.0 | **Repository:** `irushashaveen/quant-trader-python-engine`

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack & Dependencies](#2-technology-stack--dependencies)
3. [Project Structure Map](#3-project-structure-map)
4. [Application Startup Lifecycle](#4-application-startup-lifecycle)
5. [Core Configuration (Settings)](#5-core-configuration-settings)
6. [Data Layer — Three-Database Architecture](#6-data-layer--three-database-architecture)
7. [Service Layer — Deep Dive](#7-service-layer--deep-dive)
   - 7.1 [ExchangeService](#71-exchangeservice)
   - 7.2 [DataLoader (WebSocket Firehose)](#72-dataloader-websocket-firehose)
   - 7.3 [MarketDataService](#73-marketdataservice)
   - 7.4 [SMC Engine (Smart Money Concepts)](#74-smc-engine-smart-money-concepts)
   - 7.5 [AnalysisService](#75-analysisservice)
   - 7.6 [OrderFlowEngine (Alpha Filter)](#76-orderflowengine-alpha-filter)
   - 7.7 [DecisionService (9-Step Pipeline)](#77-decisionservice-9-step-pipeline)
   - 7.8 [TradeManager (Execution + Monitoring)](#78-trademanager-execution--monitoring)
8. [API Endpoint Catalogue](#8-api-endpoint-catalogue)
9. [WebSocket Streaming (Real-Time Feed)](#9-websocket-streaming-real-time-feed)
10. [Full Trade Orchestration Flow (End-to-End)](#10-full-trade-orchestration-flow-end-to-end)
11. [Schemas & Data Contracts](#11-schemas--data-contracts)
12. [Bias Scoring Formula Breakdown](#12-bias-scoring-formula-breakdown)
13. [Decision Pipeline — Threshold Reference Table](#13-decision-pipeline--threshold-reference-table)
14. [Risk Management & Position Sizing](#14-risk-management--position-sizing)
15. [Current Features Summary](#15-current-features-summary)
16. [Known Gaps & Limitations](#16-known-gaps--limitations)

---

## 1. System Overview

The **Quant Trader Python Engine** is an asynchronous, event-driven trading backend built with **FastAPI** and **Python**. It acts as the **strategy brain and execution layer** of a larger quantitative trading system.

The engine is responsible for:

| Responsibility | Description |
|---|---|
| **Market Data Ingestion** | Fetches multi-timeframe OHLCV candles from Binance USD-M Futures via CCXT |
| **Smart Money Concept (SMC) Analysis** | Detects swing points, BOS/CHoCH structure events, Fair Value Gaps, Order Blocks, and Liquidity Sweeps across multiple timeframes |
| **Order Flow Alpha Filter** | Reads funding rate, open interest, and long/short ratio to veto trades when retail sentiment is dangerously skewed |
| **Trade Decision Engine** | Converts raw market analysis into an actionable APPROVE/WAIT/REJECT decision with full risk profile |
| **Trade Execution** | Places market entry + stop loss + dual take profit bracket orders on Binance Futures |
| **Position Monitoring** | Monitors active trades in a background loop, auto-moves SL to break-even at TP1, and closes positions at TP2 or SL |
| **Real-Time WebSocket Feed** | Streams live ticker, order book (L2), trade tape, CVD, and large sweep events to frontend clients |
| **Database Persistence** | Records all trades, events, analysis snapshots, and execution reasoning across Supabase, MongoDB, and Redis |

---

## 2. Technology Stack & Dependencies

### Runtime

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Web Framework | FastAPI 0.111.0 |
| ASGI Server | Uvicorn 0.30.1 (with standard extras) |
| Async Runtime | Python `asyncio` |

### Key Libraries

| Library | Version | Purpose |
|---|---|---|
| `ccxt[pro]` | 4.3.49 | Binance exchange REST + WebSocket API (USD-M Futures) |
| `pandas` | 2.2.2 | OHLCV DataFrame manipulation and EMA calculations |
| `numpy` | 1.26.4 | Numerical operations |
| `pydantic` | ≥ 2.11.7 | Request/response validation and type enforcement |
| `pydantic-settings` | 2.3.3 | `.env`-backed typed settings |
| `redis` | 5.0.6 | Active trade state caching and idempotency |
| `motor` | 3.7.1 | Async MongoDB client (via Motor) |
| `supabase` | 2.30.1 | PostgreSQL-backed relational trade records |
| `aiohttp` | 3.9.5 | Async HTTP support |
| `websockets` | ≥ 12.0 | WebSocket protocol support |

### Infrastructure

| Service | Role |
|---|---|
| **Binance USD-M Futures** | Primary exchange (live or testnet) |
| **Redis** | Hot state: active trades, idempotency checks |
| **Supabase (PostgreSQL)** | Cold state: trade records, event logs |
| **MongoDB** | Blob storage: market analysis snapshots, execution reasoning text |
| **Docker** | Containerization (Dockerfile + docker-compose.engine.yml provided) |

---

## 3. Project Structure Map

```
quant-trader-python-engine/
├── app/
│   ├── main.py                    # FastAPI app, lifespan, router registration
│   ├── api/
│   │   ├── routes_health.py       # GET /health — liveness probe
│   │   ├── routes_market.py       # GET /api/v1/market/... — raw candles
│   │   ├── routes_analysis.py     # GET /api/v1/analysis/market-state
│   │   ├── routes_signal.py       # POST /api/v1/signal — external signal intake
│   │   ├── routes_decision.py     # GET /evaluate, POST /confirm, POST /close
│   │   └── routes_ws.py           # WS /api/v1/ws/stream/{symbol}
│   ├── core/
│   │   ├── config.py              # Pydantic Settings — all thresholds and env vars
│   │   └── logging.py             # Centralized logger
│   ├── db/
│   │   ├── supabase.py            # Supabase CRUD (trades, trade_events tables)
│   │   └── mongo.py               # MongoDB CRUD (snapshots, execution_reasoning)
│   ├── schemas/
│   │   ├── market.py              # Candle schema
│   │   ├── analysis.py            # SwingPoint, FVG, OB, BOS/CHoCH, TimeframeAnalysis, ...
│   │   ├── decision.py            # RiskProfile, TradeDecision
│   │   └── signal.py              # TradeSignal (external webhook schema)
│   ├── services/
│   │   ├── exchange_service.py    # CCXT REST wrapper (market orders, SL, TP, cancel)
│   │   ├── data_loader.py         # CCXT Pro WebSocket firehose (trades, OB, liquidations)
│   │   ├── market_data_service.py # fetch_cleaned_ohlcv, get_multi_timeframe_ohlcv, fetch_order_flow_data
│   │   ├── smc_engine.py          # Pure SMC math: swings, BOS/CHoCH, FVG, OB, bias score
│   │   ├── analysis_service.py    # analyze_market_state (wraps smc_engine, same algorithm)
│   │   ├── order_flow_engine.py   # Funding rate / L/S ratio veto logic
│   │   ├── decision_service.py    # 9-step decision pipeline → TradeDecision
│   │   ├── trade_manager.py       # execute_trade, monitor loop, manual_close
│   │   └── idempotency_service.py # Duplicate signal detection via Redis
│   └── utils/
│       └── hashing.py             # Signal hash utility
├── Dockerfile
├── docker-compose.engine.yml
├── requirements.txt
└── .env                           # Local secrets (API keys, DB URIs)
```

---

## 4. Application Startup Lifecycle

When `uvicorn app.main:app` runs, the `lifespan` async context manager orchestrates startup in four sequential steps:

```
Step 1: ExchangeService.initialize()
  └─ Creates CCXT binanceusdm async client
  └─ Verifies Hedge Mode is OFF (One-Way required)
  └─ Sets sandbox mode if BINANCE_USE_TESTNET=True

Step 2: DataLoader.start_streams(["BTC/USDT"])
  └─ Starts 3 background asyncio tasks:
     ├─ _watch_trades_loop()     → live tape → MemoryManager.add_trade() → CVD update
     ├─ _watch_order_book_loop() → L2 delta updates → MemoryManager.update_order_book()
     └─ _watch_liquidations_loop() → institutional sweeps → MemoryManager.add_liquidation()

Step 3: TradeManager.start_monitoring()
  └─ Starts _monitor_loop() as background asyncio task
  └─ Polls Redis every 5s for active_trade:* keys
  └─ Checks price vs TP1/TP2/SL for each active trade

Step 4: run_dry_run() [non-blocking background task]
  └─ Fetches 5-timeframe OHLCV for BTC/USDT
  └─ Runs SMC analysis → logs bias + confidence
  └─ Fetches order flow data → evaluates filter
  └─ Validates the entire pipeline is functional
```

**On shutdown**, the same lifespan function:
1. Calls `trade_manager.stop_monitoring()` — cancels background monitoring task
2. Calls `data_loader.stop_streams()` — cancels WebSocket tasks and closes CCXT Pro connection
3. Calls `exchange_service.close()` — closes the REST API CCXT connection

---

## 5. Core Configuration (Settings)

All settings are defined in [`config.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/core/config.py) as a Pydantic `BaseSettings` class, loaded from `.env`:

### Timeframe Weights

```
4h  → 35%   (highest weight — macro trend)
1h  → 30%   (intermediate structure)
15m → 20%   (primary timeframe for entry/SL/TP)
5m  → 10%   (entry trigger)
1m  →  5%   (lowest weight — noise filter)
```

### Decision Thresholds

| Setting | Value | Meaning |
|---|---|---|
| `VOTE_BIAS_THRESHOLD` | 0.25 | Min bias score for a TF to vote directionally |
| `VOTE_CONFLUENCE_THRESHOLD` | 0.60 | Min weighted vote ratio to approve a direction |
| `MIN_CONFIDENCE_SCORE` | 0.35 | Below → `REJECT_LOW_CONFIDENCE` |
| `WAIT_CONFIDENCE_SCORE` | 0.55 | 0.35–0.55 → `WAIT` |
| `AUTO_EXECUTE_CONFIDENCE` | 0.70 | ≥ 0.70 → full auto execution |
| `MIN_RISK_REWARD` | 2.0 | Minimum R:R to TP1 → below → `REJECT_HIGH_RISK` |
| `STOP_LOSS_BUFFER_PCT` | 0.001 | 0.1% padding beyond swing/OB level for SL |
| `TAKE_PROFIT_FIXED_R` | 2.0 | Fixed TP1 fallback = entry ± (risk × 2) |
| `TAKE_PROFIT_2_R` | 3.0 | TP2 always = entry ± (risk × 3) |

### Execution Config

| Setting | Value | Meaning |
|---|---|---|
| `DEFAULT_LEVERAGE` | 5× | Futures leverage applied before trade entry |
| `RISK_PERCENTAGE` | 1.0% | Account balance risked per trade |
| `EXECUTION_MODE` | `AUTO_EXECUTE` | `AUTO_EXECUTE` or `FORCE_MANUAL` |
| `PRIMARY_TIMEFRAME` | `15m` | Used for SL/TP calculations and no-trade checks |
| `BINANCE_USE_TESTNET` | `True` | Defaults to testnet for safety |

---

## 6. Data Layer — Three-Database Architecture

The engine uses **three separate databases** with different roles:

### 6.1 Redis (Hot State)

**Purpose:** Ultra-fast in-process state for live trade management and duplicate prevention.

**Data stored:**
- `active_trade:{symbol}` → JSON blob with full trade state (entry, SL, TP1, TP2, quantity, tp1_filled flag, sl_at_be flag, order IDs, is_simulated)
- Used by idempotency service to prevent duplicate signal processing

**Operations:**
- `r_client.set(key, json.dumps(state))` — register active trade
- `r_client.get(key)` — read trade state in monitor loop
- `r_client.delete(key)` — remove after full close (TP2 or SL hit)
- `r_client.keys("active_trade:*")` — scan all active trades

### 6.2 Supabase (PostgreSQL — Cold State)

**Purpose:** Persistent relational records of all trade lifecycle events.

**Tables:**

| Table | Fields | Purpose |
|---|---|---|
| `trades` | `id, symbol, direction, status, mode, entry_price, stop_loss, take_profit_1, take_profit_2, quantity, leverage, risk_pct, executed_at, closed_at, close_reason, final_pnl` | Main trade record — one row per trade |
| `trade_events` | `id, trade_id, event_type, value, timestamp, details` | Audit trail — N events per trade |

**Events logged:**
- `ENTRY_FILLED` — when entry order executes
- `PARTIAL_TP1_FILLED` — when TP1 is hit (partial close)
- `SL_UPDATED_TO_BE` — when stop moved to break-even
- `FULL_CLOSE` — final close (TP2, SL, or MANUAL_CLOSE)

### 6.3 MongoDB (Blob State)

**Purpose:** Storing large, unstructured snapshots that don't fit in relational tables.

**Collections:**

| Collection | Purpose |
|---|---|
| `snapshots` | Full `MarketStateAnalysisResponse` and `TradeDecision` JSON snapshots linked to each trade ID |
| `execution_reasoning` | Human-readable reason string explaining why each trade was approved/rejected |

---

## 7. Service Layer — Deep Dive

### 7.1 ExchangeService

**File:** [`exchange_service.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/exchange_service.py)

A singleton wrapper around `ccxt.async_support.binanceusdm`. Every method ensures `initialize()` is called first (lazy init pattern).

**What it does:**

| Method | What it does |
|---|---|
| `initialize()` | Creates CCXT async client, verifies One-Way position mode (rejects Hedge Mode) |
| `fetch_ohlcv(symbol, tf, limit)` | Fetches OHLCV bars from Binance Futures REST API |
| `fetch_ticker(symbol)` | Gets current bid/ask/last price |
| `create_market_order(symbol, side, amount)` | Places market entry order |
| `create_limit_order(symbol, side, amount, price)` | Places limit TP order with `reduceOnly=True` |
| `create_stop_market_order(symbol, side, amount, stop_price)` | Places `STOP_MARKET` SL order with `reduceOnly=True` |
| `cancel_orders_for_symbol(symbol)` | Cancels ALL open orders for a symbol |
| `fetch_position_details(symbol)` | Gets current futures position info |
| `close()` | Closes CCXT connection cleanly |

> **Safety:** The One-Way mode check on startup prevents the engine from placing orders on a Hedge Mode account, which would create double positions.

---

### 7.2 DataLoader (WebSocket Firehose)

**File:** [`data_loader.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/data_loader.py)

Maintains a persistent WebSocket connection to Binance via `ccxt.pro` and streams live market microstructure data into an in-memory `MemoryManager`.

**MemoryManager stores:**

| Data | Structure | Size Limit |
|---|---|---|
| Live trades (tape) | `deque(maxlen=10000)` | 10,000 trades |
| Order book | `dict` | Latest snapshot |
| Liquidations | `deque(maxlen=1000)` | 1,000 events |
| CVD (Cumulative Volume Delta) | `float` | Running total |

**How CVD is calculated:**
```
for each trade:
    if side == 'buy':  CVD += amount
    if side == 'sell': CVD -= amount
```
CVD is a footprint of aggressive buy vs sell flow. Positive CVD = buyers are dominant.

**Three background loops:**
1. `_watch_trades_loop()` — Calls `exchange.watch_trades()`, feeds each trade into `MemoryManager.add_trade()`, updates CVD in real-time
2. `_watch_order_book_loop()` — Calls `exchange.watch_order_book()`, stores L2 bid/ask data for spread and spoofing analysis
3. `_watch_liquidations_loop()` — Calls `exchange.watch_liquidations()`, stores liquidation events (institutional sweep detector)

---

### 7.3 MarketDataService

**File:** [`market_data_service.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/market_data_service.py)

Three functions that bridge exchange data into clean Pydantic `Candle` objects:

#### `fetch_cleaned_ohlcv(symbol, timeframe, limit)`
1. Fetches `limit + 1` raw OHLCV bars from Binance
2. **Drops the last (current forming) candle** — critical lookahead bias prevention
3. Converts ms timestamps to UTC datetimes
4. Returns a list of `Candle` Pydantic objects

#### `get_multi_timeframe_ohlcv(symbol, timeframes, limit)`
- Launches all timeframe fetches **concurrently** with `asyncio.gather()`
- Returns `Dict[str, List[Candle]]` — e.g. `{"4h": [...], "1h": [...], "15m": [...], ...}`

#### `fetch_order_flow_data(symbol)`
Fetches three metrics from Binance FAPI endpoints:
1. **Funding Rate** via `exchange.fetch_funding_rate()`
2. **Open Interest** via `exchange.fetch_open_interest()`
3. **Global Long/Short Account Ratio** via `exchange.fapiPublicGetGlobalLongShortAccountRatio()` with 5m period

Returns `{"funding_rate": float, "open_interest": float, "long_short_ratio": float}`. Falls back to neutral values `{0.0001, 0.0, 1.0}` in simulation mode.

---

### 7.4 SMC Engine (Smart Money Concepts)

**File:** [`smc_engine.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/smc_engine.py)

> **Note:** `smc_engine.py` and `analysis_service.py` currently contain **identical mathematical algorithms**. `smc_engine.py` is the dedicated pure-math SMC engine, while `analysis_service.py` is an older parallel implementation. The decision pipeline uses `smc_engine.run_smc_analysis()`.

The engine performs **6 sequential analysis passes** on a single timeframe's candle data:

#### Pass 1: Swing Point Detection (`detect_swings`)

Implements an N-period pivot point algorithm (default `n=3`):
- A candle is a **Swing High** if its high is strictly greater than the `n` candles on both sides
- A candle is a **Swing Low** if its low is strictly lower than the `n` candles on both sides
- Returns a raw list of `{index, timestamp, type, price, is_broken}` dicts

#### Pass 2: Sequential Structure Processing (BOS / CHoCH / Liquidity Sweeps)

Iterates over every candle chronologically (no lookahead bias). For each candle:

**Swing Confirmation:** A swing from index `k - n` is confirmed at candle `k` (ensures `n` candles have formed to the right)

**Liquidity Sweep detection:**
```
BULLISH sweep: candle wicks BELOW the last unbroken swing low, but CLOSES ABOVE it
  → Smart Money grabbed sell-side liquidity before reversing up
BEARISH sweep: candle wicks ABOVE the last unbroken swing high, but CLOSES BELOW it
  → Smart Money grabbed buy-side liquidity before reversing down
```

**Market Structure Break detection:**
```
BOS (Break of Structure): Close ABOVE last unbroken swing high (bullish continuation)
                          or Close BELOW last unbroken swing low (bearish continuation)
CHoCH (Change of Character): First BOS in the OPPOSITE direction of the previous break
                              → Signals potential trend reversal
```

After each break, all swing highs/lows AT OR BELOW/ABOVE the broken level are marked `is_broken=True`.

#### Pass 3: Fair Value Gaps (FVG)

Scans every 3-candle window (indices `i-2, i-1, i`):
```
BULLISH FVG: candle[i].low > candle[i-2].high
  → A price gap was left between the previous candle's high and current candle's low
  → Price "should" come back to fill it
  → top = candle[i].low, bottom = candle[i-2].high
  → is_mitigated = True if any future candle closes below bottom

BEARISH FVG: candle[i].high < candle[i-2].low
  → A bearish gap
  → top = candle[i-2].low, bottom = candle[i].high
  → is_mitigated = True if any future candle closes above top
```

#### Pass 4: Order Blocks (OB)

Identifies the last opposing candle before a significant expansion move:
```
Bullish OB identification:
  1. Find an expansion candle: body > 1.5× moving average of body sizes (10-period)
  2. That expansion candle must be bullish (close > open)
  3. It must also create a bullish FVG (candle[i].low > candle[i-2].high)
  4. Look back up to 4 candles from the expansion to find the last BEARISH candle
     → That bearish candle IS the Order Block (smart money institution zone)
  5. is_mitigated = True if any future close is below the OB's bottom

Bearish OB: mirror logic for bearish expansion + look back for last bullish candle
```

#### Pass 5: Buy/Sell Pressure & Trend

**Pressure metric:**
- Sums green (bullish) and red (bearish) candle body sizes over last 14 candles
- `buy_pressure = green_sum / total_sum` (range 0.0–1.0)

**Trend (EMA50-based):**
```
BULLISH: last_close > EMA50 × 1.0005 (0.05% buffer)
BEARISH: last_close < EMA50 × 0.9995
NEUTRAL: otherwise
```

#### Pass 6: Bias Scoring

Produces a single `bias_score` in range `[-1.0, +1.0]` per timeframe:

| Factor | Max Contribution | Logic |
|---|---|---|
| **Trend (EMA50)** | ±0.30 | +0.30 BULLISH, -0.30 BEARISH |
| **Structure Direction** | ±0.30 | +0.30 last BOS was BULLISH, -0.30 BEARISH |
| **Unmitigated Bullish FVGs** | +0.15 max | +0.075 per FVG, capped at 2 |
| **Unmitigated Bearish FVGs** | -0.15 max | -0.075 per FVG, capped at 2 |
| **Unmitigated Bullish OBs** | +0.15 max | +0.075 per OB, capped at 2 |
| **Unmitigated Bearish OBs** | -0.15 max | -0.075 per OB, capped at 2 |
| **Liquidity Sweep (last 5 candles)** | ±0.10 | +0.10 bullish sweep, -0.10 bearish sweep |

**Maximum possible score: +1.0 (fully bullish) / -1.0 (fully bearish)**

#### Aggregate Bias Score

After all timeframes are processed, a **weighted average** is computed:

```
aggregate_bias_score = Σ(tf_bias_score × tf_weight) / Σ(tf_weight)
```

**Confidence classification:**
- `|score| > 0.6` → **"HIGH CONFIDENCE BIAS"**
- `|score| > 0.3` → **"MODERATE CONFIDENCE BIAS"**
- otherwise → **"NEUTRAL / LOW CONFIDENCE"**

---

### 7.5 AnalysisService

**File:** [`analysis_service.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/analysis_service.py)

Contains an identical copy of the SMC algorithm, exposed via `analyze_market_state()`. Used by the `/api/v1/analysis/market-state` endpoint. Both this and `smc_engine` produce `MarketStateAnalysisResponse`.

> **Note:** This duplication is a refactor opportunity — these two files should eventually be merged.

---

### 7.6 OrderFlowEngine (Alpha Filter)

**File:** [`order_flow_engine.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/order_flow_engine.py)

A rule-based **veto filter** applied AFTER the decision pipeline approves a trade. Uses three Binance FAPI metrics as inputs:

| Input | Meaning |
|---|---|
| `long_short_ratio` | Global retail account long/short position ratio |
| `open_interest` | Total open contract value on Binance Futures |
| `funding_rate` | 8-hour funding payment rate (positive = longs pay shorts) |

**Veto rules:**

| Condition | Action | Reason |
|---|---|---|
| `long_short_ratio > 2.5` | `VETO_LONG` | Retail is crowded long → whales will hunt stops above |
| `long_short_ratio < 0.4` | `VETO_SHORT` | Retail is crowded short → short squeeze risk |
| `funding_rate > 0.001` (0.1%) | `VETO_LONG` | Market overheated long → correction likely |
| `funding_rate < -0.001` (-0.1%) | `VETO_SHORT` | Market overheated short → squeeze likely |
| Otherwise | `ALLOW` | Order flow is balanced |

If `VETO_LONG` or `VETO_SHORT` fires and it matches the proposed trade direction → the decision becomes `REJECT_HIGH_RISK` with `ORDER_FLOW_VETO_LONG/SHORT` in the `no_trade_conditions` list.

---

### 7.7 DecisionService (9-Step Pipeline)

**File:** [`decision_service.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/decision_service.py)

The core brain of the engine. Takes a `MarketStateAnalysisResponse` and `current_price` and converts them into a `TradeDecision`. The pipeline runs in strict order:

#### Step A: Per-Timeframe Voting

Each timeframe gets one directional vote based on its `bias_score`:
```
bias_score >= +0.25  → vote "LONG"
bias_score <= -0.25  → vote "SHORT"
otherwise            → vote "NEUTRAL"
```

#### Step B: Weighted Vote Confluence Check

```python
long_weight  = Σ(weight[tf] for tf if vote[tf] == "LONG")
short_weight = Σ(weight[tf] for tf if vote[tf] == "SHORT")
total_weight = Σ(weight[tf] for all tf)

long_ratio  = long_weight  / total_weight
short_ratio = short_weight / total_weight

if long_ratio  >= 0.60: direction = "LONG"
elif short_ratio >= 0.60: direction = "SHORT"
else: direction = "NONE"  → WAIT
```

#### Step C: Confidence Scoring (4 sub-factors → total 0.0 to 1.0)

| Sub-Factor | Weight | How Calculated |
|---|---|---|
| Bias Magnitude | 0.40 | `min(|aggregate_bias_score|, 1.0) × 0.40` |
| Vote Confluence | 0.30 | `confluence_ratio × 0.30` (long or short ratio, whichever applies) |
| Structure Recency | 0.15 | `0.15` if any BOS/CHoCH event exists on primary TF, else `0.0` |
| Zone Alignment | 0.15 | `0.15` if at least one unmitigated FVG or OB aligns with direction, else `0.0` |

**Maximum confidence: 1.0**

#### Step D: No-Trade Conditions (Hard Overrides)

Two conditions are checked on the **primary timeframe (15m)**:

1. **`LIQUIDITY_NOT_SWEPT`**: Checks last 5 liquidity sweeps for an opposing-side sweep
   - If going LONG: needs a BULLISH sweep (swept sell-side lows) to confirm institutional reversal
   - If not present: flags `LIQUIDITY_NOT_SWEPT`

2. **`PRICE_IN_OPPOSING_IMBALANCE`**: 
   - If going LONG: checks if current price is inside an unmitigated BEARISH FVG
   - If inside AND there's no confirming BULLISH BOS → flags `PRICE_IN_OPPOSING_IMBALANCE`

#### Steps E & F: Risk Profile Calculation

Computed only when confidence is above the WAIT threshold:

**Stop Loss calculation** (structure-first):
1. For LONG: Find highest unbroken swing low below price. Find highest unmitigated bullish OB bottom below price.
2. Compare: prefer OB if it's tighter than swing but not implausibly tight (≥ 0.3× swing distance)
3. Apply 0.1% buffer beyond the chosen level
4. Fallback: 1.5% fixed stop if no structure found

**Take Profit calculation** (structure-first):
- TP1: Nearest aligned FVG midpoint or OB boundary that clears minimum R:R of 2.0
  - LONG: nearest unmitigated bullish FVG midpoint above entry, or nearest unmitigated bearish OB bottom above entry
  - Fallback: entry ± (risk × 2.0) if no structure target meets 2.0 R:R
- TP2: Always entry ± (risk × 3.0), regardless of structure

#### Steps F & G: Gates (Rejection Conditions)

| Gate | Trigger | Decision Output |
|---|---|---|
| No confluence | `direction == "NONE"` | `WAIT` |
| Below minimum confidence | `score < 0.35` | `REJECT_LOW_CONFIDENCE` |
| In wait zone | `0.35 ≤ score < 0.55` | `WAIT` |
| Poor R:R | `rr_ratio < 2.0` | `REJECT_HIGH_RISK` |
| Hard no-trade conditions | `LIQUIDITY_NOT_SWEPT` or `PRICE_IN_OPPOSING_IMBALANCE` | `REJECT_HIGH_RISK` |
| Order flow veto | VETO matches direction | `REJECT_HIGH_RISK` |

#### Step H: Manual Confirmation Flag

```
confidence_score < 0.70 → requires_manual_confirmation = True
confidence_score >= 0.70 → requires_manual_confirmation = False (full auto)
```

#### Step I: Final Output

If all gates pass, outputs either `APPROVE_LONG` or `APPROVE_SHORT` with a full `RiskProfile`.

---

### 7.8 TradeManager (Execution + Monitoring)

**File:** [`trade_manager.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/services/trade_manager.py)

The execution and lifecycle management layer. A singleton `trade_manager` instance is shared across the app.

#### `calculate_position_size(symbol, entry, stop_loss)`

Dynamic sizing based on account balance and SL distance:
```python
risk_amount = usdt_free_balance × (RISK_PERCENTAGE / 100)   # e.g. 1% of balance
sl_distance_pct = abs(entry - stop_loss) / entry
notional = risk_amount / sl_distance_pct
raw_qty = notional / entry
qty = exchange.amount_to_precision(symbol, raw_qty)   # rounds to exchange-valid qty
```

Falls back to hardcoded safe minimums if balance fetching fails or API keys are absent.

#### `execute_trade(symbol, direction, entry, sl, tp1, tp2, snapshots)`

Full bracket order orchestration:
1. **Checks Redis** — aborts if `active_trade:{symbol}` already exists (prevents double entry)
2. **Calculates position size** — dynamic or simulated
3. **Places orders** on Binance:
   - Entry: **MARKET order** (buy or sell, immediately filled)
   - Stop Loss: **STOP_MARKET order** with `reduceOnly=True` 
   - TP1: **LIMIT order** for 50% of quantity with `reduceOnly=True`
   - TP2: **LIMIT order** for remaining 50% with `reduceOnly=True`
4. **Writes to Supabase** — creates trade record + logs `ENTRY_FILLED` event
5. **Saves snapshots to MongoDB** — analysis + decision JSON, execution reasoning text
6. **Registers in Redis** — stores full trade state for the monitor loop

#### `_monitor_loop()` — Background Trade Monitor

Runs as a background asyncio task, polling every **5 seconds**:
```
for each active_trade:* key in Redis:
    fetch current price from exchange
    check: tp1_hit, tp2_hit, sl_hit

    TP1 Hit (price reaches take_profit_1):
      → Move Stop Loss to Break-Even (entry price)
      → Cancel old SL order on exchange
      → Place new STOP_MARKET at entry price
      → Update state in Redis + Supabase

    TP2 Hit (price reaches take_profit_2):
      → Cancel all open orders
      → Calculate PnL
      → Update Supabase trade to CLOSED (close_reason="TAKE_PROFIT_2")
      → Delete from Redis

    SL Hit (price hits stop_loss):
      → Cancel all open orders
      → Calculate PnL (negative)
      → Update Supabase trade to CLOSED (close_reason="STOP_LOSS")
      → Delete from Redis
```

#### `manual_close_position(symbol)`

Emergency/manual close:
1. Reads trade state from Redis
2. Cancels all exchange orders
3. Places market close order in opposite direction
4. Fetches current price, calculates PnL
5. Updates Supabase to `CLOSED` with `close_reason="MANUAL_CLOSE"`
6. Deletes from Redis

---

## 8. API Endpoint Catalogue

### Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Welcome message |
| `GET` | `/health` | Liveness probe → returns `{"status": "ok"}` |

### Analysis

| Method | Path | Key Parameters | Description |
|---|---|---|---|
| `GET` | `/api/v1/analysis/market-state` | `symbol`, `limit`, `timeframes[]` | Runs full SMC analysis on live data → returns `MarketStateAnalysisResponse` |

### Signal (External Webhook)

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/api/v1/signal` | `TradeSignal` JSON | Receives external signal, runs full analysis + decision pipeline, auto-executes or returns for confirmation |

### Decision (Primary Engine Endpoint)

| Method | Path | Body/Params | Description |
|---|---|---|---|
| `GET` | `/api/v1/decision/evaluate` | `symbol`, `limit`, `timeframes[]` | Full live pipeline: fetch → analyze → decide → optionally auto-execute |
| `POST` | `/api/v1/decision/confirm` | `ConfirmTradeRequest` | Manually confirm and execute a pending trade |
| `POST` | `/api/v1/decision/close` | `CloseTradeRequest` | Emergency close any active position |

### WebSocket

| Method | Path | Description |
|---|---|---|
| `WS` | `/api/v1/ws/stream/{symbol}` | Real-time ticker + order book + trade tape + CVD + sweep events (broadcast every 150ms) |

---

## 9. WebSocket Streaming (Real-Time Feed)

**File:** [`routes_ws.py`](file:///c:/Users/irush/Documents/GitHub/quant-trader-python-engine/app/api/routes_ws.py)

Each connected frontend client gets its own isolated WebSocket handler with:

**4 concurrent background asyncio tasks per connection:**
1. `watch_ticker_loop()` — Streams live price/high/low/volume
2. `watch_trades_loop()` — Streams live trade tape, calculates CVD
3. `watch_orderbook_loop()` — Streams top 15 bids/asks (L2)
4. `broadcast_loop()` — Aggregates all data and sends JSON to client **every 150ms**

**In-memory `WSMemoryManager`:**
- Stores last 10,000 trades in a deque
- Detects large sweep events: any trade with notional ≥ $30,000 USDT is classified as a "sweep event"
- CVD calculated per trade: buy = +amount, sell = −amount

**Payload broadcast structure:**
```json
{
  "ticker": { "price": 65000.0, "high": 65500.0, "low": 64800.0, "volume": 12000.0 },
  "order_book": { "bids": [[65000, 2.5], ...], "asks": [[65001, 1.2], ...] },
  "trades": [{ "timestamp": ..., "side": "BUY", "price": 65000.0, "amount": 0.5 }, ...],
  "cvd": 1234.56,
  "sweeps": [{ "timestamp": ..., "side": "BUY", "size": 5.0, "price": 64900.0, "notional": 324500.0 }]
}
```

---

## 10. Full Trade Orchestration Flow (End-to-End)

```
CLIENT → POST /api/v1/signal  (or GET /api/v1/decision/evaluate)
         │
         ├─[Idempotency Check]── Redis lookup for duplicate signal hash
         │                        └─ If duplicate → 409 Conflict
         │
         ├─[Market Data]──────── get_multi_timeframe_ohlcv("BTC/USDT", ["1m","5m","15m","1h","4h"], 100)
         │                        ├─ 5 concurrent OHLCV fetches from Binance REST
         │                        └─ Each drops active candle (lookahead bias safe)
         │
         ├─[SMC Analysis]──────── run_smc_analysis(symbol, timeframe_data)
         │                        ├─ Pass 1: detect_swings (n=3 pivot)
         │                        ├─ Pass 2: BOS/CHoCH structure events + liquidity sweeps
         │                        ├─ Pass 3: Fair Value Gaps
         │                        ├─ Pass 4: Order Blocks
         │                        ├─ Pass 5: Buy/Sell pressure + EMA50 trend
         │                        ├─ Pass 6: Per-TF bias score [-1.0, +1.0]
         │                        └─ Weighted aggregate bias score
         │
         ├─[Order Flow Filter]─── fetch_order_flow_data → evaluate_order_flow
         │                        ├─ Funding rate (Binance FAPI)
         │                        ├─ Open interest (Binance FAPI)
         │                        ├─ L/S ratio (Binance FAPI 5m)
         │                        └─ Output: ALLOW / VETO_LONG / VETO_SHORT
         │
         ├─[Decision Pipeline]─── evaluate_trade_decision(analysis, price, order_flow)
         │                        ├─ A: Per-TF votes
         │                        ├─ B: Weighted confluence → direction or NONE
         │                        ├─ C: Confidence score (4 sub-factors)
         │                        ├─ D: No-trade conditions (liquidity + imbalance)
         │                        ├─ E: SL calculation (swing or OB, + 0.1% buffer)
         │                        ├─ F: TP calculation (structure-first, 2R/3R fallback)
         │                        ├─ G: R:R gate (min 2.0)
         │                        ├─ H: Confidence gate (reject/wait/approve/auto)
         │                        └─ I: Order flow veto check
         │
         ├─[Execution Decision]──
         │    WAIT / REJECT ─────────────────────────────────────→ Return decision only
         │    APPROVE + manual_confirmation ──────────────────────→ Return "AWAITING_CONFIRMATION"
         │    APPROVE + confidence >= 0.70 ──────────────────────→ AUTO_EXECUTE
         │                                                              │
         ├─[Trade Execution]────────────────────────────────────────────┘
         │    ├─ Check Redis: active_trade:{symbol} exists? → skip
         │    ├─ calculate_position_size → 1% account risk / SL distance
         │    ├─ Place MARKET entry order (Binance)
         │    ├─ Place STOP_MARKET SL order
         │    ├─ Place LIMIT TP1 (50% qty)
         │    ├─ Place LIMIT TP2 (50% qty)
         │    ├─ Write trade record to Supabase
         │    ├─ Log ENTRY_FILLED event to Supabase
         │    ├─ Save analysis + decision snapshots to MongoDB
         │    └─ Register trade state in Redis
         │
         └─[Background Monitor Loop]─ polling every 5s
              ├─ TP1 hit → move SL to Break-Even, update Redis + Supabase
              ├─ TP2 hit → close position, log PnL, remove from Redis
              └─ SL hit  → close position, log PnL, remove from Redis
```

---

## 11. Schemas & Data Contracts

### `Candle`
```
timestamp: datetime, open: float, high: float, low: float, close: float, volume: float
```

### `TimeframeAnalysis`
```
trend: "BULLISH" | "BEARISH" | "NEUTRAL"
swings: List[SwingPoint]
structure_events: List[MarketStructureEvent]  (BOS / CHoCH)
liquidity_sweeps: List[LiquiditySweep]
fvgs: List[FairValueGap]
order_blocks: List[OrderBlock]
buy_pressure: float [0.0, 1.0]
sell_pressure: float [0.0, 1.0]
bias_score: float [-1.0, 1.0]
```

### `MarketStateAnalysisResponse`
```
symbol: str
limit: int
aggregate_bias_score: float [-1.0, 1.0]
timeframe_analyses: Dict[str, TimeframeAnalysis]
confidence: str  ("HIGH CONFIDENCE BIAS" | "MODERATE CONFIDENCE BIAS" | "NEUTRAL / LOW CONFIDENCE")
```

### `RiskProfile`
```
entry_price: float
stop_loss: float
stop_loss_source: "SWING" | "ORDER_BLOCK"
take_profit_1: float
take_profit_1_source: "FVG" | "ORDER_BLOCK" | "FIXED_2R"
take_profit_2: float        (always 3R)
risk_reward_ratio: float
risk_pct: float             (placeholder, 1.0)
```

### `TradeDecision`
```
symbol: str
decision: "APPROVE_LONG" | "APPROVE_SHORT" | "WAIT" | "REJECT_LOW_CONFIDENCE" | "REJECT_HIGH_RISK"
direction: "LONG" | "SHORT" | "NONE"
confidence_score: float [0.0, 1.0]
aggregate_bias_score: float [-1.0, 1.0]
reason: str
risk_profile: Optional[RiskProfile]
no_trade_conditions: List[str]
requires_manual_confirmation: bool
timeframe_votes: Dict[str, "LONG" | "SHORT" | "NEUTRAL"]
timestamp: datetime
execution_status: "IDLE" | "AWAITING_CONFIRMATION" | "AUTO_EXECUTED" | "MANUALLY_EXECUTED" | None
executed_at: Optional[str]
trade_id: Optional[str]
```

---

## 12. Bias Scoring Formula Breakdown

```
Per-Timeframe Bias Score  =  trend_factor
                           + structure_factor
                           + fvg_factor
                           + ob_factor
                           + sweep_factor

Where:
  trend_factor      = +0.30 (BULLISH) | -0.30 (BEARISH) | 0.0 (NEUTRAL)
  structure_factor  = +0.30 (last break BULLISH) | -0.30 (BEARISH) | 0.0 (none)
  fvg_factor        = min(N_bullish_FVG × 0.075, +0.15) - min(N_bearish_FVG × 0.075, 0.15)
  ob_factor         = min(N_bullish_OB × 0.075, +0.15) - min(N_bearish_OB × 0.075, 0.15)
  sweep_factor      = +0.10 (recent bullish sweep) | -0.10 (bearish sweep) | 0.0

Clamped to [-1.0, 1.0]

Aggregate = Σ(bias_score[tf] × weight[tf]) / Σ(weight[tf])
```

---

## 13. Decision Pipeline — Threshold Reference Table

```
confidence_score    outcome
─────────────────────────────────────────────────────
< 0.35              REJECT_LOW_CONFIDENCE
0.35 – 0.54         WAIT
0.55 – 0.69         APPROVE (requires_manual_confirmation = True)
≥ 0.70              APPROVE (full auto execution)
─────────────────────────────────────────────────────

R:R to TP1          outcome
─────────────────────────────────────────────────────
< 2.0               REJECT_HIGH_RISK
≥ 2.0               Pass (TP2 always 3R regardless)
─────────────────────────────────────────────────────

Order Flow          outcome
─────────────────────────────────────────────────────
L/S > 2.5           VETO_LONG (if direction = LONG)
L/S < 0.4           VETO_SHORT (if direction = SHORT)
Funding > 0.1%      VETO_LONG
Funding < -0.1%     VETO_SHORT
Otherwise           ALLOW
─────────────────────────────────────────────────────
```

---

## 14. Risk Management & Position Sizing

The engine implements a **fixed fractional risk model**:

```
1. Determine free USDT balance from Binance account
2. risk_amount = balance × 1%
3. sl_distance_pct = |entry - stop_loss| / entry
4. notional = risk_amount / sl_distance_pct
5. quantity = notional / entry
6. quantity = round_to_exchange_precision(quantity)
```

**Example (BTC/USDT, $10,000 balance):**
```
risk_amount        = $10,000 × 1%  = $100
entry              = $65,000
stop_loss          = $64,350  (1% below = $650 distance)
sl_distance_pct    = $650 / $65,000 = 1.0%
notional           = $100 / 0.01 = $10,000
quantity           = $10,000 / $65,000 = 0.154 BTC
```

**Bracket structure:**
- Entry: 100% market fill
- SL: 100% at stop level (`STOP_MARKET`)
- TP1: 50% at first target (`LIMIT`)
- TP2: 50% at second target (`LIMIT`)
- After TP1 fills: SL moves to break-even automatically

---

## 15. Current Features Summary

| Feature | Status | Notes |
|---|---|---|
| Multi-timeframe OHLCV ingestion | ✅ Implemented | 1m, 5m, 15m, 1h, 4h — concurrent fetch |
| Lookahead-bias-safe candle processing | ✅ Implemented | Active candle always dropped |
| Swing point detection | ✅ Implemented | N-period pivot algorithm |
| BOS/CHoCH structure event detection | ✅ Implemented | Sequential, no lookahead |
| Fair Value Gap detection | ✅ Implemented | Bullish + Bearish, mitigation tracked |
| Order Block detection | ✅ Implemented | Expansion-based, mitigation tracked |
| Liquidity sweep detection | ✅ Implemented | Wick-beyond, close-within pattern |
| EMA50-based trend classification | ✅ Implemented | 0.05% buffer for NEUTRAL zone |
| Buy/Sell pressure metric | ✅ Implemented | 14-candle body size ratio |
| Per-timeframe bias score | ✅ Implemented | 6-factor weighted score |
| Multi-TF aggregate bias score | ✅ Implemented | Weighted by TF importance |
| Funding rate filter | ✅ Implemented | ±0.1% veto thresholds |
| Long/short ratio filter | ✅ Implemented | < 0.4 or > 2.5 veto |
| Open interest tracking | ✅ Implemented | Fetched, stored, not yet used in decision |
| 9-step decision pipeline | ✅ Implemented | Full gating with reason text |
| Structure-first SL calculation | ✅ Implemented | Swing or OB-based, 0.1% buffer |
| Structure-first TP calculation | ✅ Implemented | FVG/OB targets, 2R/3R fallback |
| R:R gating | ✅ Implemented | Min 2.0 to TP1 |
| Confidence score gating | ✅ Implemented | 3-zone: reject/wait/approve |
| Auto-execute vs manual flag | ✅ Implemented | Threshold at 0.70 confidence |
| Dynamic position sizing | ✅ Implemented | 1% risk model |
| Bracket order placement | ✅ Implemented | Entry + SL + TP1(50%) + TP2(50%) |
| Break-even SL management | ✅ Implemented | Auto-moves SL at TP1 |
| Background trade monitoring | ✅ Implemented | 5-second polling loop |
| Manual position close | ✅ Implemented | Emergency endpoint |
| External signal webhook | ✅ Implemented | `POST /api/v1/signal` |
| Idempotency (duplicate signal guard) | ✅ Implemented | Redis-based hash check |
| WebSocket real-time feed | ✅ Implemented | Ticker + OB + tape + CVD + sweeps |
| Simulation/testnet mode | ✅ Implemented | Full mock path when no API keys |
| Supabase trade persistence | ✅ Implemented | trades + trade_events tables |
| MongoDB snapshot storage | ✅ Implemented | Analysis + decision + reasoning |
| Redis active trade state | ✅ Implemented | Hot state with full trade context |
| Docker containerization | ✅ Implemented | Dockerfile + compose file |
| Hedge Mode detection & rejection | ✅ Implemented | Startup safety check |

---

## 16. Known Gaps & Limitations

| Gap | Description | Impact |
|---|---|---|
| **`smc_engine.py` and `analysis_service.py` duplication** | Both files contain identical SMC algorithms. Should be merged. | Maintenance risk — a bug fix in one won't apply to the other |
| **No trailing stop loss** | SL only moves to break-even at TP1. No trailing beyond that. | Leaves money on the table in trending markets |
| **Open interest not used in scoring** | Fetched but not integrated into bias or confidence score | Missed alpha signal |
| **DataLoader memory not used in decision** | The live CVD, L2 data, and liquidation data from `data_loader.py` are captured but not wired into the decision pipeline | The WebSocket firehose is effectively unused by the decision engine |
| **Single-symbol default stream** | `start_streams(["BTC/USDT"])` is hardcoded at startup | Multi-asset strategies not supported |
| **No trade history analytics** | No API endpoint to query past trades from Supabase | Dashboard integration requires raw DB queries |
| **TP1/TP2 fill detection is price-based only (simulation)** | In simulation mode, TP/SL fills are detected by comparing price vs levels. In live mode, exchange order status is not polled to verify actual fills | Could miss partially filled orders in live mode |
| **No re-entry logic** | After a SL close, the engine will not re-enter even if conditions remain valid until the next explicit signal | Misses continuation moves |
| **Leverage hardcoded to 5×** | `DEFAULT_LEVERAGE = 5` is not dynamic per symbol or volatility | Inappropriate for low-volatility assets |
| **`PRIMARY_TIMEFRAME` always 15m** | SL/TP/no-trade checks always use 15m data regardless of requested timeframes | Could cause issues if 15m is not in the requested timeframe list |
| **No backtesting mode** | The engine is designed for live execution only — no historical simulation framework exists | Cannot validate strategy historically |
| **No notification system** | No Telegram/Discord/email alerts for trade events | Manual monitoring required |

---

*Report generated by reading and analyzing all 22 source files of the quant-trader-python-engine project.*
