# Python Engine Project Start Guide

## Main idea
Your **Next.js dashboard** and **Python Engine** should be built as **two separate projects**.

That matches your architecture:
- Next.js dashboard = UI and operator control
- n8n = orchestration and automation flow
- Python Engine (FastAPI) = execution, market data, strategy logic, trade manager

## Recommended project split

### 1. Dashboard project
Use your existing Next.js app for:
- Bot activation buttons
- Live engine status
- Open trades view
- Logs and alerts
- Risk mode display
- Manual actions like close position / pause bot

### 2. Python Engine project
Create a new separate folder/repo called:

```bash
python-engine
```

Use it only for:
- FastAPI service
- Pydantic schemas
- CCXT / CCXT Pro exchange client
- Signal validation
- Strategy calculations
- Trade manager
- Database writes
- Health checks
- WebSocket consumers

## Best folder structure

```bash
python-engine/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── security.py
│   ├── api/
│   │   ├── routes_health.py
│   │   ├── routes_signal.py
│   │   ├── routes_bot.py
│   │   ├── routes_positions.py
│   │   └── routes_risk.py
│   ├── schemas/
│   │   ├── signal.py
│   │   ├── bot.py
│   │   ├── trade.py
│   │   └── risk.py
│   ├── services/
│   │   ├── exchange_service.py
│   │   ├── market_stream.py
│   │   ├── strategy_service.py
│   │   ├── execution_service.py
│   │   ├── trade_manager.py
│   │   ├── idempotency_service.py
│   │   └── macro_risk_service.py
│   ├── db/
│   │   ├── supabase.py
│   │   ├── mongo.py
│   │   └── redis.py
│   └── utils/
│       ├── hashing.py
│       ├── timeframes.py
│       └── validators.py
├── tests/
├── .env
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## How the projects talk to each other

### Flow
1. User clicks button in **Next.js dashboard**
2. Dashboard sends request to **n8n** or directly to **FastAPI**
3. n8n can enrich the signal with AI/news context
4. Python Engine validates and executes
5. Python Engine stores result in DB
6. Dashboard reads status/logs from DB or engine API

## Best way to start building
Build the Python Engine in phases.

### Phase 1 — foundation
Create only these first:
- `GET /health`
- `POST /signal`
- `POST /bot/activate`
- `GET /positions`
- `POST /positions/close`

At this phase, do not connect real Binance trading yet.
Just validate payloads and return mock responses.

### Phase 2 — engine core
Add:
- Pydantic validation
- config management
- structured logging
- idempotency hashing
- Redis duplicate check

### Phase 3 — market connection
Add:
- Binance testnet
- CCXT / CCXT Pro
- WebSocket stream manager
- basic order placement test

### Phase 4 — strategy logic
Add:
- swing detection
- FVG detection
- order block logic
- MTF alignment with shifted HTF data
- VSA calculations

### Phase 5 — trade lifecycle
Add:
- break-even trailing
- partial take profit logic
- emergency close
- bot state machine

### Phase 6 — production connection
Add:
- Supabase writes
- MongoDB writes
- n8n webhook auth
- dashboard integration
- metrics and alerts

## Commands to start the Python Engine project

### 1. Create folder
```bash
mkdir python-engine
cd python-engine
```

### 2. Create virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install starter packages
```bash
pip install fastapi uvicorn pydantic pydantic-settings python-dotenv aiohttp redis
```

Later you add:
```bash
pip install ccxt pandas numpy scipy motor asyncpg
```

### 4. Save requirements
```bash
pip freeze > requirements.txt
```

### 5. Run local development server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

### 6. Open FastAPI docs
```text
http://localhost:8001/docs
```

That `/docs` page becomes your first testing UI.

## Minimal first API design

### Health
- `GET /health`
- returns engine status, version, uptime

### Bot control
- `POST /bot/activate`
- `POST /bot/deactivate`
- `GET /bot/status`

### Signals
- `POST /signal`
- validates incoming trade signal

### Positions
- `GET /positions`
- `POST /positions/close`
- `POST /positions/close-all`

### Risk
- `GET /risk-mode`
- `POST /risk-mode/recalculate`

## Example usage model

### Development UI
Use:
- FastAPI `/docs`
- terminal logs
- Postman or curl

### Real operator UI
Use:
- Next.js dashboard

### Background automation
Use:
- n8n webhooks
- TradingView alerts
- cron jobs / workers

## Very important rule
Do **not** build the dashboard inside the Python Engine.
Keep them separate.

Correct setup:
- `quant-dashboard` = frontend app
- `python-engine` = backend microservice

That separation is already consistent with your architecture because the dashboard triggers actions while the Python engine performs validation, execution, market streaming, and trade management independently.

## First milestone recommendation
Your first milestone should be:
- Python Engine runs locally
- `/docs` works
- `/health` works
- `/signal` accepts valid JSON
- duplicate signal hash check works
- Next.js dashboard can call `/health`

After that, move to Binance testnet only.
