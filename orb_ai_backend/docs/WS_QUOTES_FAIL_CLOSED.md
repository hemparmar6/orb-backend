# WebSocket `/api/v1/ws/quotes` — live market-data fail-closed behaviour

## Why

The quote broadcaster (`app/ws/quote_broadcaster.py`) is backed **exclusively**
by the deterministic `MockMarketDataProvider`. It exists for dev preview and the
test-suite. If the socket kept fanning those mock ticks out while a **real**
market-data provider is configured (e.g. `MARKET_DATA_PROVIDER=upstox`), clients
would receive **simulated prices presented as live market data** — a serious
correctness/safety defect for a trading product.

## Rule

The endpoint reads `settings.MARKET_DATA_PROVIDER` at connection time:

| `MARKET_DATA_PROVIDER` | Behaviour |
| --- | --- |
| `mock` | Mock streaming remains available (subscribe / receive / ping). |
| anything else (`upstox`, `dhan`, `kotak_neo`, …) | **Fail closed.** |

When it must fail closed the server:

1. Sends one error frame:
   ```json
   {"type": "error", "data": {"code": "market_data_provider_unavailable",
     "message": "Live market-data streaming is unavailable for the configured provider; refusing to emit mock ticks."}}
   ```
2. Closes the socket with **application close code `4503`**
   (`CLOSE_PROVIDER_UNAVAILABLE`, RFC 6455 4000–4999 range).

The gate runs **after** JWT authentication but **before** the broadcaster (and
therefore the mock provider) is ever started, so no mock tick can leak.

## Implementation

- `app/api/v1/ws/quotes.py`
  - `_provider_is_mock()` — single source of truth for the gate.
  - `CLOSE_PROVIDER_UNAVAILABLE = 4503`.
- Tests: `tests/test_ws_quotes_fail_closed.py`.

## Client guidance (mobile / admin-web)

A `4503` close (or the `market_data_provider_unavailable` error frame) means the
live WebSocket feed is intentionally unavailable for the current server
configuration. Clients should fall back to the REST market-data endpoints
(`/api/v1/market-data/...`), which already fail closed with HTTP `503` and a
`market_data_provider_unavailable` body under the same conditions. Do **not**
retry the socket in a tight loop.
