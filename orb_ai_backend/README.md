# ORB AI — Backend

Production-quality FastAPI backend for the **ORB AI** algorithmic trading platform.

Current version delivers **Modules 1 + 2 + 3 + 4 + 5 + 6 + 7 + 8**:

- **Module 1 (foundation)** — User auth (JWT + bcrypt), user/strategy/settings/manual-trade models, PostgreSQL, Docker, versioned REST, Swagger, structured logging.
- **Module 2 (trading engine framework)** — Modular paper-trading engine: market-data provider abstraction (mock + historical), risk engine, order manager (LIMIT/SL/SL-M/OCO), portfolio manager, trade logger, strategy engine with lifecycle + registry, sample MA-cross strategy.
- **Module 3 (broker abstraction)** — `BrokerAdapter` ABC, `MockLiveBroker` (fully working simulated wire), Dhan + Kotak Neo skeletons, Fernet-encrypted per-user broker credentials, `BrokerOrderStream` (WS + polling fallback + Redis pub/sub of order updates), per-session `execution_mode: "paper" | "live"`, `LiveBrokerExecutor` that routes engine orders to any broker adapter.
- **Module 4 (client-facing WebSockets)** — `WS /api/v1/ws/quotes`, `WS /api/v1/ws/orders`, `WS /api/v1/ws/notifications`. JWT auth via `?token=`, token-bucket rate limiting, subscribe/unsubscribe/ping protocol, graceful degradation when Redis is disabled.
- **Module 5 (ORB strategy + backtester)** — Full **Opening Range Breakout** strategy (`orb`) on the existing engine (long/short, configurable OR window, SL, target, trailing SL, re-entry, max trades/day, daily loss limit, risk-based sizing) — **broker-independent**. Candle-driven backtester with every requested KPI (total return, net/gross P&L, win rate, profit factor, max drawdown, Sharpe, long/short + monthly breakdowns, equity curve). REST API: `POST /backtest/run`, `GET /backtest/history`, `GET /backtest/{id}[/results]`, `GET /backtest/{id}/export?format=json|csv`.
- **Module 7 (Admin dashboard)** — RBAC admin APIs, audit log, admin SPA mount.
- **Module 8 (Portfolio, Analytics, Risk, Notifications, Reports)** — see [Module 8](#module-8) below.
- **Module 9 (AI Trading Intelligence)** — see [Module 9](#module-9) below.

Deliberately **out of scope**:
- Real Dhan / Kotak Neo HTTP wiring (skeletons only; every method raises `BrokerNotImplementedError` with an actionable message until credentials + implementation are provided).
- Live market-data feeds.
- Real-money trading.
- ORB-specific strategy logic.

---

## Table of contents

- [Requirements](#requirements)
- [Quick start (Docker)](#quick-start-docker)
- [Local development (without Docker)](#local-development-without-docker)
- [Environment variables](#environment-variables)
- [Project structure](#project-structure)
- [API reference](#api-reference)
- [Trading engine — internals](#trading-engine--internals)
- [Database migrations](#database-migrations)
- [Testing](#testing)
- [Deploying to an Ubuntu VPS](#deploying-to-an-ubuntu-vps)
- [Security notes](#security-notes)

---

## Requirements

- Python **3.12+**
- PostgreSQL **15+**
- Redis **7+** (optional at runtime — the engine boots without it)
- Docker & docker-compose (for containerized runs)

## Quick start (Docker)

```bash
cd orb_ai_backend
cp .env.example .env
# Edit .env: set a strong JWT_SECRET_KEY and (optionally) ENCRYPTION_KEY.

docker compose up --build
# → api on http://localhost:8000  (docs at /docs)
# → postgres on :5432
# → redis    on :6379
```

The `api` container waits for Postgres, runs Alembic migrations, then starts Uvicorn.

## Local development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate

# 1) Core dependencies — public PyPI only, must succeed on any clean machine.
pip install -r requirements.txt

# 2) Optional AI provider (Module 9). Fetches from the Emergent index.
#    Safe to skip: the AIService transparently falls back to the deterministic
#    rule-based provider when this package is not installed.
pip install -r requirements-ai.txt || \
  echo "[orb-ai] AI extras skipped — rule-based fallback will be active."

# Postgres (via Docker)
docker run -d --name orb_pg -p 5432:5432 \
  -e POSTGRES_USER=orb -e POSTGRES_PASSWORD=orb -e POSTGRES_DB=orb_ai postgres:15

# Optional Redis
docker run -d --name orb_redis -p 6379:6379 redis:7-alpine

cp .env.example .env
# Set DATABASE_URL=postgresql+asyncpg://orb:orb@localhost:5432/orb_ai
# Set REDIS_URL=redis://localhost:6379/0 (or leave REDIS_ENABLED=false)

alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment variables

See `.env.example` for the full list. Highlights added in Module 2:

| Variable                       | Description                                         | Default          |
| ------------------------------ | --------------------------------------------------- | ---------------- |
| `REDIS_URL`                    | Redis connection string                             | `redis://localhost:6379/0` |
| `REDIS_ENABLED`                | If false, `get_redis()` returns `None` cleanly      | `false`          |
| `ENCRYPTION_KEY`               | Fernet key (used from Module 3 for broker creds)    | —                |
| `MARKET_DATA_PROVIDER`         | Default provider name; only `mock` ships in M2      | `mock`           |
| `MOCK_TICK_INTERVAL_MS`        | Cadence of the mock provider's tick loop            | `250`            |
| `MOCK_RANDOM_SEED`             | Deterministic seed for reproducible ticks           | `42`             |
| `TRADING_SESSION_START/END`    | RiskEngine trading window (HH:MM)                   | `00:00 / 23:59`  |
| `TRADING_SESSION_TIMEZONE`     | Timezone for the window                             | `Asia/Kolkata`   |

**Module 9 (AI Trading Intelligence) — all optional:**

| Variable                       | Description                                         | Default          |
| ------------------------------ | --------------------------------------------------- | ---------------- |
| `AI_ENABLED`                   | Master switch; `false` forces rule-based fallback   | `true`           |
| `AI_PROVIDER`                  | `gpt52` \| `rule_based`                             | `gpt52`          |
| `AI_MODEL`                     | Model id for the primary provider                   | `gpt-5.2`        |
| `EMERGENT_LLM_KEY`             | Emergent Universal LLM key. Missing → fallback only | —                |
| `AI_CACHE_TTL_S`               | Redis cache TTL for AI responses                    | `600`            |
| `AI_REQUEST_TIMEOUT_S`         | Per-request LLM timeout before falling back        | `25`             |

## Project structure

```
orb_ai_backend/
├── app/
│   ├── main.py
│   ├── core/                       config, security, redis, logging, exceptions
│   ├── db/                         async engine + session + base
│   ├── models/                     user, trade (manual), strategy, settings, engine (Module 2)
│   ├── schemas/                    Pydantic DTOs
│   ├── repositories/               Repository pattern
│   ├── services/                   Business logic (Module 1 resources)
│   ├── engine/                     ★ Module 2 — trading engine
│   │   ├── market_data/            MarketDataProvider ABC + Mock + Historical + registry
│   │   ├── strategy/               BaseStrategy + registry + StrategyManager + samples/
│   │   ├── risk/                   RiskEngine (pre-order checks)
│   │   ├── orders/                 OrderManager + PaperExecutor
│   │   ├── portfolio/              PortfolioManager (positions, P&L)
│   │   ├── logger.py               TradeLogger (one row per fill)
│   │   └── runner.py               EngineRunner — glue
│   ├── middleware/
│   └── api/v1/                     versioned REST — auth, users, settings, strategies,
│                                    manual-trades, trading, positions, orders, pnl, trades
├── alembic/                        0001_initial, 0002_trading_engine
├── tests/                          pytest suite (27 tests)
├── scripts/                        entrypoint.sh, nginx.conf.example
├── Dockerfile
├── docker-compose.yml              (api + postgres + redis)
└── ...
```

## API reference

Auto-generated docs at `/docs` (Swagger UI), `/redoc`, `/openapi.json`.

### Module 1 endpoints

| Method | Path                                | Auth | Purpose                              |
| ------ | ----------------------------------- | ---- | ------------------------------------ |
| POST   | `/api/v1/auth/register`             | —    | Create account                       |
| POST   | `/api/v1/auth/login`                | —    | Bearer tokens                        |
| POST   | `/api/v1/auth/refresh`              | —    | Rotate refresh token                 |
| POST   | `/api/v1/auth/logout`               | JWT  | Revoke current refresh token         |
| GET/PATCH | `/api/v1/users/me`               | JWT  | Profile                              |
| POST   | `/api/v1/users/me/change-password`  | JWT  | Change password                      |
| GET/PUT| `/api/v1/settings/me`               | JWT  | User settings                        |
| CRUD   | `/api/v1/strategies`                | JWT  | Strategy definitions                 |
| CRUD   | `/api/v1/manual-trades`             | JWT  | User-authored trade records          |

### Module 2 — trading engine

| Method | Path                                | Auth | Purpose                              |
| ------ | ----------------------------------- | ---- | ------------------------------------ |
| POST   | `/api/v1/trading/start`             | JWT  | Create + start an engine session (paper or live) |
| POST   | `/api/v1/trading/stop`              | JWT  | Stop a session or all user sessions  |
| GET    | `/api/v1/trading/status`            | JWT  | List sessions + registered strategies|
| GET    | `/api/v1/orders`                    | JWT  | Paper orders (filters: status, symbol, session_id) |
| GET    | `/api/v1/positions`                 | JWT  | Paper positions (only_open, session_id) |
| GET    | `/api/v1/trades`                    | JWT  | Paper trade execution log            |
| GET    | `/api/v1/pnl`                       | JWT  | Realized + unrealized + counts       |

### Module 3 — broker accounts

| Method | Path                                      | Auth | Purpose                            |
| ------ | ----------------------------------------- | ---- | ---------------------------------- |
| GET    | `/api/v1/brokers/catalog`                 | —    | List brokers + required creds keys |
| POST   | `/api/v1/brokers/connect`                 | JWT  | Register a broker (Fernet-encrypt) |
| GET    | `/api/v1/brokers`                         | JWT  | List the user's broker accounts    |
| GET    | `/api/v1/brokers/{id}`                    | JWT  | Broker account details             |
| DELETE | `/api/v1/brokers/{id}`                    | JWT  | Disconnect                         |
| GET    | `/api/v1/brokers/{id}/funds`              | JWT  | Live funds/margin snapshot         |
| GET    | `/api/v1/brokers/{id}/positions`          | JWT  | Live positions snapshot            |
| GET    | `/api/v1/brokers/{id}/orders`             | JWT  | Live order book                    |

**Order placement is not exposed on `/brokers/*`.** All trading goes through the Trading Engine (`/trading/start` with `execution_mode: "live"` + `broker_account_id`).

### Module 4 — client-facing WebSockets

| Method | Path                     | Auth                   | Purpose                                     |
| ------ | ------------------------ | ---------------------- | ------------------------------------------- |
| WS     | `/api/v1/ws/quotes`      | JWT via `?token=<jwt>` | Live market-data ticks (subscribe by symbol)|
| WS     | `/api/v1/ws/orders`      | JWT via `?token=<jwt>` | Live order updates for the user's sessions  |

**Protocol (JSON frames)**

Client → server:
```json
{ "action": "subscribe",   "channel": "quotes", "symbols": ["A", "B"] }
{ "action": "unsubscribe", "channel": "quotes", "symbols": ["A"] }
{ "action": "subscribe",   "channel": "orders", "session_ids": ["<uuid>"] }
{ "action": "ping" }
```

Server → client:
```json
{ "type": "quote",            "data": { "symbol": "A", "price": 101.2, "volume": 1, "ts": "..." } }
{ "type": "order_update",     "data": { ...reconciled broker update... } }
{ "type": "subscription_ack", "data": { "channel": "quotes", "action": "subscribe", "symbols": [...] } }
{ "type": "pong" }
{ "type": "error",            "data": { "code": "unauthorized" | "rate_limited" | "redis_unavailable" | ..., "message": "..." } }
```

**Close codes** — `4401` auth failed · `4429` rate limited · `4500` internal · `4501` dependency (Redis) unavailable.

**Rate limiting** — Token-bucket per socket (default 20 msgs/s burst, 5/s refill). Excess messages close the socket with 4429.

**Redis dependency** — `/ws/v1/orders` requires `REDIS_ENABLED=true` (it consumes `orders:{engine_session_id}` channels published by `BrokerOrderStream`). When Redis is off, the socket sends an error frame and closes with 4501 rather than hanging. `/ws/v1/quotes` runs from the in-process `QuoteBroadcaster` and does **not** require Redis.

**Auto-subscription** — On connect, `/ws/v1/orders` auto-subscribes to every `engine_session` owned by the authenticated user. The client can narrow / broaden via `subscribe` / `unsubscribe` messages; server-side filtering ensures a user can never subscribe to another user's session id.

### Module 5 — ORB strategy + backtester

**ORB strategy** — registered as `orb`. Broker-independent — signals go through the standard `StrategyContext`, so paper mode uses the in-process `PaperExecutor` and live mode routes through any registered `BrokerAdapter` via `LiveBrokerExecutor` (Module 3). Configurable parameters (all optional, defaults shown):

| Param                       | Default          | Description                                      |
| --------------------------- | ---------------- | ------------------------------------------------ |
| `symbols`                   | `["NIFTY"]`      | Universe                                         |
| `opening_range_minutes`     | `15`             | OR window                                        |
| `session_start` / `_end`    | `09:15` / `15:15`| HH:MM in `timezone`                              |
| `timezone`                  | `Asia/Kolkata`   |                                                  |
| `enable_long` / `_short`    | `true` / `true`  | Direction toggles                                |
| `stop_loss_pct`             | `0.5`            | % from entry                                     |
| `target_pct`                | `1.5`            | % from entry                                     |
| `trailing_stop_pct`         | `null`           | Trails SL when set                               |
| `re_entry_enabled`          | `false`          | Enable re-entries after exit                     |
| `max_re_entries_per_day`    | `0`              | Cap on re-entries                                |
| `max_trades_per_day`        | `4`              | Hard cap on entries                              |
| `daily_loss_limit`          | `5000`           | Absolute — halts the day if breached             |
| `risk_per_trade_pct`        | `1.0`            | % of capital risked (with `use_risk_based_sizing`)|
| `quantity`                  | `1.0`            | Fallback lot size                                |
| `use_risk_based_sizing`     | `false`          | Compute qty from risk / SL distance              |
| `fee_per_trade`             | `0`              | Flat fee (backtest only)                         |
| `slippage_pct`              | `0`              | Slippage on entry & exit (backtest only)         |

**Backtester** — synchronous candle replay in `app/engine/backtest/`. Produces:

- Trade log (one row per closed round-trip)
- Equity curve (post-trade equity + one EOD point per day)
- Metrics: `total_return_pct`, `net_profit`, `gross_profit`, `gross_loss`, `win_rate_pct`, `avg_profit`, `avg_loss`, `profit_factor`, `max_drawdown` (+ `_pct`), `sharpe_ratio` (annualised √252), `number_of_trades`, `long_stats`, `short_stats`, `monthly_performance`, `final_equity`

**API endpoints** (all JWT-protected):

| Method | Path                                | Purpose                                        |
| ------ | ----------------------------------- | ---------------------------------------------- |
| POST   | `/api/v1/backtest/run`              | Run synchronously; returns full trades + KPIs  |
| GET    | `/api/v1/backtest/history`          | Paginated list (most-recent-first)             |
| GET    | `/api/v1/backtest/{id}`             | Full payload                                   |
| GET    | `/api/v1/backtest/{id}/results`     | Hoisted metrics view                           |
| GET    | `/api/v1/backtest/{id}/export?format=json\|csv` | Downloadable report                |

**Sample request** — `POST /api/v1/backtest/run`:

```json
{
  "strategy_name": "orb",
  "symbols": ["NIFTY", "BANKNIFTY"],
  "start_date": "2026-05-01T00:00:00+00:00",
  "end_date":   "2026-06-01T00:00:00+00:00",
  "initial_capital": 100000,
  "params": {
    "opening_range_minutes": 15,
    "session_start": "09:15",
    "session_end":   "15:15",
    "enable_long":  true,
    "enable_short": true,
    "stop_loss_pct": 0.5,
    "target_pct":    1.5,
    "trailing_stop_pct": 0.75,
    "re_entry_enabled": true,
    "max_re_entries_per_day": 1,
    "max_trades_per_day": 3,
    "daily_loss_limit": 5000,
    "quantity": 25,
    "use_risk_based_sizing": false,
    "fee_per_trade": 20,
    "slippage_pct": 0.02
  }
}
```

**Note on candle source.** Until a real historical provider is wired (Dhan/Kotak), the backtester uses a deterministic synthetic candle generator (`app/engine/backtest/synthetic_candles.py`, seeded per-symbol). When a real historical adapter arrives, only `services/backtest_service.py::_load_candles` needs to change.

**`POST /api/v1/trading/start`** now accepts `execution_mode` and `broker_account_id`:

```json
{
  "strategy_name": "demo_ma_cross",
  "symbols": ["A", "B"],
  "params": { "fast": 5, "slow": 20, "quantity": 10, "stop_loss_pct": 0.5, "target_pct": 1.5 },
  "initial_capital": 100000,
  "risk_config": {
    "max_position_size": 200,
    "max_daily_loss": 5000,
    "max_risk_per_trade_pct": 1.5,
    "trading_session_start": "09:15",
    "trading_session_end": "15:30",
    "trading_session_timezone": "Asia/Kolkata"
  },
  "provider": "mock",
  "execution_mode": "paper",           // or "live"
  "broker_account_id": null             // required when execution_mode="live"
}
```

## Broker abstraction (Module 3)

**Design goal:** the trading engine only imports `app.brokers.base` (the ABC). Concrete adapters register themselves at import time.

```python
from app.brokers import register_broker, BrokerAdapter

@register_broker("myothersbroker")
class MyOtherBrokerAdapter(BrokerAdapter):
    @classmethod
    def required_credentials(cls) -> list[str]:
        return ["api_key"]

    async def health_check(self): ...
    async def place_order(self, req): ...
    async def modify_order(self, broker_order_id, **kwargs): ...
    # ... etc
```

**Adapters shipped in Module 3:**
- `mock_live` — fully working simulated wire (immediate fills, WS-style update stream). Use it for tests + demos + local paper→live smoke.
- `dhan` — skeleton. Every method raises `BrokerNotImplementedError` with an actionable message + the exact Dhan v2 endpoint URL to wire.
- `kotak_neo` — skeleton with the same shape.

**Credential storage.** `POST /api/v1/brokers/connect` validates the payload against the adapter's `required_credentials()`, then encrypts the whole dict with Fernet (`ENCRYPTION_KEY`) and stores only the ciphertext. Plaintext is decrypted **only** at adapter-instantiation time (inside `BrokerService.build_adapter`). Nothing ever returns the plaintext.

**Live-mode flow.**

```
POST /trading/start (execution_mode="live", broker_account_id=X)
         │
         ▼
StrategyManager
  ├─ BrokerService.build_adapter(X)  → decrypt creds, instantiate adapter
  ├─ EngineRunner(..., execution_mode="live", broker_adapter=A)
  │      └─ each tick: OrderManager(executor=LiveBrokerExecutor(A))
  │             ↑
  │             │  Strategy.on_tick → ctx.place_order → RiskEngine.check
  │             │  → LiveBrokerExecutor.on_place → adapter.place_order
  │             │  → broker_order_id stored on PaperOrder
  │             │  → try_fill_from_quote returns None (fills come from stream)
  │             ▼
  └─ BrokerOrderStream(adapter=A, on_update=apply_broker_update)
             ├─ WS primary   (adapter.stream_order_updates)
             └─ Poll fallback (adapter.get_order for open broker_order_ids)
             ▼
        OrderManager.apply_broker_update
             → position + realized P&L (same code path as paper)
             → TradeLogger.log
             → Redis PUBLISH orders:{engine_session_id}  (if REDIS_ENABLED)
```

**Poll fallback interval:** `BrokerOrderStream(poll_interval_s=3.0)` by default. WS is attempted first; if it raises `NotImplementedError` or disconnects, polling takes over transparently.

**Real-time client feed (future):** every reconciled order update is published to Redis on channel `orders:{engine_session_id}`. Module 4 will consume this from a client-facing WebSocket endpoint.

## Trading engine — internals

**Data flow**

```
MarketDataProvider ──▶ EngineRunner ──▶ BaseStrategy.on_tick(quote)
                                │             │
                                │             ▼
                                │      OrderIntentRequest  ────┐
                                │             │                │
                                ▼             ▼                ▼
                        OrderManager  ◀── RiskEngine.check ──  reject ▶ persist REJECTED
                              │
                              ▼
                        PaperExecutor.try_fill(order, quote)
                              │  Fill                Fill? None:
                              ▼                        skip
                        Update PaperOrder ▶ apply_fill ▶ PortfolioManager
                                                            │
                                                            ▼
                                                    TradeLogger.log ▶ PaperTrade row
```

**Pluggable seams (Module 3 will drop into these without touching core):**

- `MarketDataProvider` ABC (`app/engine/market_data/base.py`) + `register_provider("dhan")` — future Dhan/Kotak feeds slot in via the same interface.
- `StrategyContext` — strategies talk to the engine only through this. They never import DB models directly.
- `BrokerAdapter` (planned in Module 3) — will slot into `EngineRunner` in place of the current in-process `PaperExecutor`.

**Order types supported**

- `MARKET` — fills at LTP on next tick.
- `LIMIT` — fills when LTP crosses (BUY: LTP ≤ limit; SELL: LTP ≥ limit).
- `SL`  — stop-loss with limit price; triggers on `trigger_price`, then behaves as LIMIT.
- `SL_M` — stop-loss market; triggers on `trigger_price`, then behaves as MARKET.
- **OCO** — if the entry has `stop_loss` and/or `target_price`, the OrderManager auto-spawns child SL_M / LIMIT exits on the entry's fill. Whichever fills first, the other is auto-cancelled.

**Risk checks (pre-order)**

1. Trading window (HH:MM, timezone-aware).
2. Max daily loss (blocks new entries; exits still allowed).
3. Position sizing hook (defaults to as-requested; strategies can override).
4. Max position size per symbol.
5. Max risk per trade (notional as % of current capital).

**Sample strategy: `demo_ma_cross`**

Simple MA-cross demo (registered as `demo_ma_cross`). Configurable `fast`, `slow`, `quantity`, `stop_loss_pct`, `target_pct`. BUY MARKET on fast-crosses-above-slow while flat; SELL MARKET (close) on the reverse cross.

## Database migrations

```bash
alembic revision --autogenerate -m "add something"
alembic upgrade head
alembic downgrade -1
```

Migrations shipped:
- `0001_initial` — users, user_settings, strategies, trades (manual)
- `0002_trading_engine` — engine_sessions, paper_orders, paper_positions, paper_trades
- `0003_broker_accounts` — broker_accounts + `execution_mode` / `broker_account_id` on `engine_sessions` and `paper_orders`
- `0004_backtest` — backtest_runs

## Testing

```bash
pytest -q
```

**74 tests, all passing.** Coverage:
- Auth: register, login, refresh rotation, logout revocation, protected routes
- Resources: strategies + manual-trades + settings CRUD
- Market data: deterministic mock, snapshot, registry lookup, historical candles
- Risk engine: allow/block for each rule, position sizer hook, trading window
- Order manager: MARKET / LIMIT crossing, cancel, SL_M trigger, OCO auto-cancel, weighted-avg + realized P&L
- Trading flow (paper): end-to-end via API — start session, drive ticks through demo_ma_cross, verify orders/positions/trades/PnL, stop session
- Crypto: Fernet round-trip, missing-key + tampered-token failure modes
- Broker registry: adapter registration, credential validation, unsupported broker → 400
- Broker skeletons: Dhan + Kotak Neo raise `BrokerNotImplementedError` with actionable messages
- MockLive broker: end-to-end place → auto-fill → position → funds → WS stream
- Broker API: connect (Fernet-encrypt), list, get, delete, duplicate 409, credential validation, funds/positions/orders snapshots, cross-user isolation
- Live-mode execution: 400 without broker_account_id, then end-to-end run — strategy → LiveBrokerExecutor → MockLive → BrokerOrderStream → reconciled fill visible in `/orders`, `/trades`, `/positions`
- WebSockets — `/ws/v1/quotes`: reject-without-token, subscribe/receive tick, unsubscribe, wrong-channel error frame. `/ws/v1/orders`: reject-without-token, graceful `redis_unavailable` + 4501 close when Redis is disabled.
- Backtester engine — long target hit, short SL hit, EOD square-off, max-trades-per-day cap, daily loss halt, trailing stop lock-in, synthetic candles reproducible + ordered.
- Backtester metrics — pure-wins profit_factor=inf, mixed wins/losses with drawdown, monthly grouping, drawdown % from peak, Sharpe returns 0 on flat curve, empty input.
- Backtester API — auth required, happy-path run + full KPI shape, GET/results, history pagination (most-recent-first), JSON + CSV export headers/schema, validation (bad dates → 422), unknown strategy → 404, cross-user isolation.

## Deploying to an Ubuntu VPS

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone <your-repo> orb_ai_backend
cd orb_ai_backend
cp .env.example .env
# Set: APP_ENV=production, strong JWT_SECRET_KEY, real Postgres creds, real ENCRYPTION_KEY, CORS_ORIGINS
docker compose up -d --build
# Front with Nginx (see scripts/nginx.conf.example)
```

## Security notes

- Passwords hashed with bcrypt (cost 12).
- JWT HS256, refresh-token rotation with per-user `jti`, revoked on logout / password change.
- All timestamps UTC (`datetime.now(timezone.utc)`).
- CORS locked via `CORS_ORIGINS`.
- Rate limiting deferred to Nginx (`limit_req`) or a later module.
- `ENCRYPTION_KEY` scaffolded (unused until Module 3) — a single Fernet key you can later swap for a cloud secret manager without touching the crypto module.

---

© ORB AI. Modules 1 + 2 + 3 — Backend foundation + trading engine framework + broker abstraction.

---

## Module 8

Module 8 adds production-grade portfolio management, analytics, risk, notifications, and PDF/CSV reporting on top of the existing paper engine. It reuses `PaperTrade`, `PaperPosition`, `EngineSession`, `BacktestRun` and `BrokerAccount` — no data duplication.

### New REST endpoints (all under `/api/v1`)

| Path | Method | Purpose |
| --- | --- | --- |
| `/portfolio/summary` | GET | Equity, realized/unrealized P&L, exposure, open positions |
| `/portfolio/holdings` | GET | Open positions with mark-to-market |
| `/portfolio/allocation` | GET | Capital allocation by symbol |
| `/portfolio/performance/daily?days=N` | GET | Daily P&L for last N days |
| `/portfolio/performance/monthly?months=N` | GET | Monthly P&L for last N months |
| `/portfolio/snapshot` | POST | Force-create today's `PortfolioSnapshot` |
| `/analytics/summary` | GET | Win-rate, profit factor, expectancy, R:R, drawdown |
| `/analytics/equity-curve` | GET | Cumulative equity per trade |
| `/analytics/drawdown` | GET | Drawdown series |
| `/analytics/monthly` | GET | Monthly performance breakdown |
| `/analytics/journal` | GET | Trade journal (paginated) |
| `/risk/dashboard` | GET | Combined risk snapshot |
| `/risk/exposure/symbol` | GET | Gross/net exposure per symbol |
| `/risk/exposure/broker` | GET | Exposure per broker |
| `/risk/daily` | GET | Today's realised P&L + trade count |
| `/risk/drawdown` | GET | Max drawdown (absolute + %) |
| `/risk/margin` | GET | Capital, gross exposure, utilisation, free capital |
| `/risk/position-sizing` | GET | Per-symbol position weight vs capital |
| `/notifications` | GET | Paginated inbox (supports `unread_only=true`) |
| `/notifications/{id}/read` | POST | Mark one as read |
| `/notifications/read-all` | POST | Mark all as read |
| `/notifications/preferences` | GET / PUT | Per-user channel + event preferences |
| `/notifications/test` | POST | Send a test notification to yourself |
| `/reports/preview/{type}` | GET | Preview data that would be included |
| `/reports/generate/{type}?format=pdf\|csv` | GET | Download the generated file |
| `/reports/history` | GET | List past report generations |

Supported report types: `daily`, `weekly`, `monthly`, `portfolio`, `trade_history`, `backtest`, `strategy_performance`, `risk`, `broker_activity`, `pnl`.

### New WebSocket

- `WS /api/v1/ws/notifications` — authenticated with `?token=`, streams
  `{"type":"notification", "data":{...}}` frames whenever a notification
  is created for the user.

### Notification service architecture

Unified `NotificationService` fans out one logical event to any/all of:

- Email — **Resend** (`RESEND_API_KEY`) with **SMTP** (`SMTP_*`) as
  fallback if Resend is not configured.
- Telegram — Bot API (`TELEGRAM_BOT_TOKEN`), per-user `chat_id` in
  preferences (falls back to `TELEGRAM_CHAT_ID` env if set).
- Push — Emergent-managed push (`EMERGENT_PUSH_*`) — only works after
  the mobile build is deployed.

Every provider degrades gracefully: if credentials are missing, the
provider is disabled and the channel is marked `not_configured` in
`Notification.channel_status`. Transient failures are retried
`NOTIFICATIONS_MAX_RETRIES` times per channel.

Users may override delivery per event via
`PUT /notifications/preferences` with
`event_overrides: {"trade_executed": false}` etc.

### Reports engine

- Server-side PDF generation with **ReportLab** — no frontend deps.
- Reusable `ReportContext` shared between PDF, CSV and future formats
  (XLSX interface reserved).
- Streaming download from the API; large trade lists paginate at the
  data layer (`ReportDataLayer.iter_trades`, batch=500) and are capped
  at 1 000 rows in the PDF (CSV is uncapped, up to `REPORTS_MAX_ROWS`).
- Every generation is audit-logged (`action="report.generated"`).
- Templates: cover → portfolio → analytics → risk → optional backtest
  → trade history. Header/footer + page numbers on every page.

### Background snapshots

`python -m app.services.tasks` snapshots every active user's portfolio
into `portfolio_snapshots`. Wire it to a cron / K8s CronJob / APScheduler
job — it is idempotent per (user, date).

### Environment variables (Module 8)

See `.env.example` for the full list — Resend, SMTP, Telegram, Push,
`NOTIFICATIONS_MAX_RETRIES`, `REPORTS_COMPANY_NAME`, etc.

### Tests

Module 8 ships with 26 API tests + 2 service tests covering every
endpoint, retry semantics, graceful-degradation of missing providers,
and PDF/CSV output. Full suite is **215 tests, 100% green**:

```
pytest -q --deselect tests/test_admin_dashboard_shim.py
```

(The admin-dashboard-shim test requires a built React bundle and is
unrelated to Module 8.)

### Background scheduler (APScheduler)

Set `SCHEDULER_ENABLED=true` to activate in-process recurring jobs:

| Job ID | Default cron | Purpose |
| --- | --- | --- |
| `daily_portfolio_snapshot` | `0 18 * * *` | Persist `PortfolioSnapshot` per active user (idempotent per date). |
| `weekly_pnl_digest` | `0 6 * * MON` | Generates a weekly PDF report per eligible user + delivers it through `NotificationService`. Every run is audit-logged with `action="report.scheduled.generated"` (or `report.scheduled.failed`). |
| `notification_retry` | `*/5 * * * *` | Retries notifications whose last dispatch failed on any channel. Bounded by `NOTIFICATIONS_MAX_RETRIES * 2`. |

Admin endpoints:

- `GET  /api/v1/scheduler/status` — show enabled state + next run times.
- `POST /api/v1/scheduler/trigger/{job_id}` — run one of the jobs immediately.

Both require an admin JWT.

### Weekly P&L Digest (modular)

`app.services.tasks.weekly_digest.run_weekly_digest()`:

1. Selects eligible users via a swappable `WeeklyDigestFeatureGate`
   (default: every active user). Swap the gate later to check a Stripe
   subscription tier, plan flag, org role, etc. — the reporting pipeline
   never changes.
2. Builds a `ReportContext` via `ReportDataLayer` (same one used by the
   `/reports/generate/*` endpoint).
3. Renders a PDF via `PDFReportBuilder`, persists a `ReportRun` row
   (auditable), and delivers the notification through `NotificationService`
   so any channel the user has enabled (in-app, email, Telegram, push)
   receives the digest automatically.
4. Records `action="report.scheduled.generated"` in the audit log.

Both the on-demand admin endpoint (`POST /scheduler/trigger/weekly_pnl_digest`)
and the cron-scheduled invocation call the exact same code path.


---

## Module 9

**AI Trading Intelligence.** Adds an advisory-only AI layer on top of the
deterministic trading engine.

### Contents

| Area | New surface |
|---|---|
| **AI Trade Journal** | Auto-scores every closed trade (quality, entry, exit, risk, compliance, emotional flags, improvements). |
| **Strategy Optimisation** | Parameter, walk-forward, multi-symbol, multi-timeframe, batch jobs with ranked results. |
| **Advanced Backtesting** | Walk-forward, Monte Carlo, commission / slippage / variable spread, portfolio + multi-strategy backtests. |
| **Market Intelligence** | Regime, trend strength, volatility, liquidity, gap behaviour, session stats. |
| **AI Analytics** | Win probability, expected value, Sharpe / Sortino, drawdown, equity curve, snapshots. |
| **Recommendations** | Position sizing / risk reduction / capital allocation / strategy / portfolio-balancing — **advisory only**. |
| **Mobile app** | New tab **AI** → AI Overview + Trade Review + Recommendations. |
| **Admin dashboard** | New sidebar section **AI** with 5 pages (Analytics, Optimisation Jobs, Strategy Performance, Recommendation Centre, Market Intelligence). |
| **WebSocket** | `/api/v1/ws/ai` — push events for reviews, recommendations, optim status. |

### Key architectural guarantees

* AI is **advisory only**. No AI code path can place, modify or cancel a
  trade. The trading engine does not import `app.ai.*`.
* **Provider-agnostic.** `AIService` uses GPT-5.2 (via the Emergent
  Universal LLM key) by default; swap in another provider by adding a
  class under `app.ai.providers/` and toggling `AI_PROVIDER`.
* **Optional.** If `EMERGENT_LLM_KEY` is missing or `AI_ENABLED=false`,
  every AI endpoint transparently degrades to the deterministic
  **rule-based** provider. The app boots normally — **no startup failure**.
* **Versioned prompts** live in `app/ai/prompts/` — bump `PROMPT.version`
  when the JSON schema changes; old cache entries expire, no code churn.
* **Cache** — Redis (via `app.core.redis.get_redis()`), TTL controlled
  by `AI_CACHE_TTL_S`. Missing Redis = cache disabled, requests still work.
* **Audit** — every AI request is persisted to `ai_audit_log` with
  provider, model, source (`primary` / `fallback` / `cached`), and
  request metadata (never PII / credentials).

### Configuration (all optional)

| Env var | Default | Purpose |
|---|---|---|
| `AI_ENABLED` | `true` | Master switch. `false` forces rule-based fallback. |
| `AI_PROVIDER` | `gpt52` | `gpt52` \| `rule_based`. |
| `AI_MODEL` | `gpt-5.2` | Model identifier for the primary provider. |
| `EMERGENT_LLM_KEY` | *(unset)* | Emergent Universal LLM key. Missing → rule-based fallback. |
| `AI_CACHE_TTL_S` | `600` | Redis cache TTL for AI responses. |
| `AI_REQUEST_TIMEOUT_S` | `25` | Per-request LLM timeout before falling back. |

See `.env.example` for a copy-paste template.

### REST endpoints

Base prefix: `/api/v1`

* `GET /ai/analytics/portfolio` — metrics + equity curve.
* `GET /ai/analytics/win-probability?last_n=100`
* `GET /ai/analytics/expected-value`
* `POST /ai/analytics/snapshot` — persist an analytics snapshot.
* `GET /ai/trade-review[?limit=50]` — list AI reviews for the user.
* `GET /ai/trade-review/{trade_id}` — latest review.
* `POST /ai/trade-review/{trade_id}/generate` — idempotent per `(trade_id, prompt_version)`.
* `GET /ai/recommendations` — pending recommendations.
* `POST /ai/recommendations/generate` — new recommendations from current stats.
* `POST /ai/recommendations/{id}/action` — body `{"status": "accepted" | "dismissed"}`.
* `POST /optimisation/jobs` — body `{strategy_id, kind, params_space, config}`.
* `GET /optimisation/jobs`, `GET /optimisation/jobs/{id}`, `GET /optimisation/jobs/{id}/results?top=20`.
* `GET /market-intelligence/{symbol}` — latest snapshot.
* `POST /market-intelligence/{symbol}/snapshot?timeframe=D1` — compute + persist.

### WebSocket

`WS /api/v1/ws/ai?token=<jwt>` — same auth as every other Module-4 socket.

### Alembic

Migration `0008_module9_ai` (chains from `0007_subscriptions`) adds 7 tables:

```
ai_trade_reviews · ai_recommendations · ai_audit_log ·
optim_jobs · optim_results · mi_snapshots · ai_analytics_snapshots
```

No existing table is modified. Rollback: `alembic downgrade 0007_subscriptions`.

### Tests

30 new tests under `tests/module9/`:

* `test_ai_service.py` — fallback / timeout / disabled / no-key scenarios.
* `test_prompts_and_fallback.py` — every prompt registered + rule-based coverage.
* `test_analytics.py` — metrics, equity curve, Monte Carlo, walk-forward, cost model, portfolio backtest.
* `test_market_intelligence.py` — regime / trend / volatility / liquidity / gap / session.
* `test_api_module9.py` — JWT auth, portfolio snapshot, rule-based recommendation generation, optimisation job lifecycle.

Run: `pytest tests/module9/`. Full suite (Modules 1-9): `pytest tests/`.
