# Upstox Market Data (V3) — Provider Guide

Upstox is a **first-class ORB market-data provider**, wired into the same
`MarketDataProvider` abstraction as Dhan, Kotak Neo and Mock. The mobile app and
the trading engine consume normalised `Quote` objects and never see anything
Upstox-specific — every existing `/api/v1/market-data/...` contract is unchanged.

- Provider name / `MARKET_DATA_PROVIDER` value: **`upstox`**
- Final provider set: `mock`, `dhan`, `kotak_neo`, `upstox`
- Source: `app/engine/market_data/upstox.py`
- Protobuf decoder: `app/engine/market_data/upstox_protobuf.py`
- Instrument-key resolver: `app/engine/market_data/upstox_instruments.py`
- Instrument-master loader: `app/brokers/instruments/upstox.py`

---

## Configuration

Added to `app/core/config.py` (Pydantic `Settings`) — nothing hard-coded:

| Variable | Type | Default | Purpose |
| --- | --- | --- | --- |
| `MARKET_DATA_PROVIDER` | str | `mock` | Set to `upstox` to select this provider. |
| `UPSTOX_ENABLED` | bool | `false` | Explicit feature flag for Upstox. |
| `UPSTOX_ACCESS_TOKEN` | str | *(empty)* | Daily Upstox V3 access token (secret). |

**Security:** the access token is only ever sent in the `Authorization: Bearer`
header of the authorize / LTP REST calls. It is **never** logged, **never** put
in the WebSocket URL, **never** returned from `get_stats()`, **never** exposed to
the frontend, and is masked in exceptions (`_mask()`).

**Fail-closed:** if `MARKET_DATA_PROVIDER=upstox` but no token is present, the
provider raises on construction instead of silently serving mock data. A real
provider selected explicitly must never fall back to fake prices.

---

## Enabling Upstox in a non-production environment

> Do **not** change Railway production for this. Use a staging/dev `.env` only.

```bash
# staging/dev .env
MARKET_DATA_PROVIDER=upstox
UPSTOX_ENABLED=true
UPSTOX_ACCESS_TOKEN=<your daily V3 access token>
```

Obtain the daily access token via the standard Upstox OAuth login flow
(`https://api.upstox.com/v2/login/authorization/token`). The token is valid for
the trading day and must be refreshed daily.

---

## Instrument-key format

Upstox identifies every instrument by a single string key: `"<SEGMENT>|<id>"`.

| ORB symbol | Upstox instrument key |
| --- | --- |
| `NIFTY` | `NSE_INDEX|Nifty 50` |
| `BANKNIFTY` | `NSE_INDEX|Nifty Bank` |
| `FINNIFTY` | `NSE_INDEX|Nifty Fin Service` |
| `SENSEX` | `BSE_INDEX|SENSEX` |
| Equity (e.g. TCS) | `NSE_EQ|INE467B01029` (ISIN-keyed) |
| F&O contract | `NSE_FO|<token>` |

Resolution layers (`resolve_instrument_key`):

1. Curated static map for the core index universe (NIFTY / BANKNIFTY / …).
2. Pass-through for values that are already valid instrument keys.
3. For equities / F&O, resolve keys from the **Upstox instrument master**
   (`UpstoxInstrumentLoader`, `complete.json.gz`) and hand them to the provider
   via `upstox_symbol_map(master, symbols)`. Unknown, non-key symbols are
   rejected — keys are never guessed.

---

## How to test live market data

Automated (no credentials, no network — mocks the authorize REST, the LTP REST
and `websockets.connect`):

```bash
pytest tests/test_market_data_upstox.py tests/test_instruments_upstox.py -q
```

Manual smoke test against real Upstox (staging token required):

```python
import asyncio
from app.engine.market_data import get_provider

async def main():
    p = get_provider("upstox", credentials={"access_token": "<token>"}, mode="full")
    # NIFTY / BANKNIFTY are pre-mapped; equities/F&O come from the master.
    await p.subscribe(["NIFTY", "BANKNIFTY"])
    # Optional: seed an initial snapshot from the V3 LTP REST endpoint.
    print(await p.prime_snapshots(["NIFTY", "BANKNIFTY"]))
    await p.start()
    n = 0
    async for q in p.stream():
        print(q.symbol, q.price, q.volume, q.ts)
        n += 1
        if n >= 5:
            break
    await p.stop()

asyncio.run(main())
```

---

## Health / observability

- `provider.status` → `UpstoxStatus`: `connecting`, `connected`, `disconnected`,
  `authentication_failed`, `subscription_failed`, `provider_error`.
- `provider.get_stats()` → ticks received/dropped, decode errors, reconnects,
  latency (min/avg/max), `status`, `mode` — **never** the token.
- Connect / disconnect / reconnect events also flow through the existing
  `BrokerHealthTracker` (keyed by provider name `upstox`) that backs
  `GET /api/v1/monitoring/broker-health`, because the shared
  `ReconnectingWSClient` reports them automatically.

## Token expiration & refresh (audit)

**Credential type in use:** a single daily **access token** (`UPSTOX_ACCESS_TOKEN`),
obtained via Upstox's OAuth2 authorization-code login flow.

**Refresh behavior (per current Upstox docs):**
- The standard Upstox access token is valid until **~03:30 AM IST the next day**
  and is then invalidated.
- Upstox's standard OAuth flow does **not** issue a long-lived `refresh_token`
  that can silently renew a market-data access token. (Upstox offers optional
  read-only *extended tokens* for some account types, but these are not
  universally available and are out of scope here.)

**Conclusion — no refresh mechanism was invented.** Because the app stores only
the access token (not the full OAuth client secret + fresh authorization code),
automatic in-place refresh is **not possible** with this credential shape. Daily
renewal is an operational step:

1. Re-run the Upstox OAuth login to mint a new daily access token.
2. Update `UPSTOX_ACCESS_TOKEN` in the staging secret store.
3. The provider picks up the new token on its next (re)connect; on
   `authentication_failed` it surfaces that status and the reconnect loop
   retries with backoff (no reconnect storm).

If, in future, full login credentials are stored server-side, a daily
re-login scheduler could be added following the existing
`KotakSidRefreshScheduler` pattern — but that is a deliberate, separate change,
not implemented here.

## Staging validation status

Automated tests (mocked authorize REST, LTP REST, historical REST, and
`websockets.connect`) fully pass with no network/credentials. **Real staging
validation** (live NIFTY/BANKNIFTY ticks over the actual Upstox V3 socket)
requires a valid daily `UPSTOX_ACCESS_TOKEN` **and** NSE market hours
(~09:15–15:30 IST) and has NOT yet been performed in this environment.

