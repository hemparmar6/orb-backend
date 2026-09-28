"""Trading engine.

Public entry point is `StrategyManager` (in ``app/engine/strategy/manager.py``).
Sub-packages:

- ``market_data``  — abstract provider, mock provider, historical candles
- ``strategy``     — BaseStrategy, StrategyContext, StrategyManager, registry, samples
- ``risk``         — RiskEngine (pre-order checks)
- ``orders``       — OrderManager + PaperExecutor (fills, SL/TP, LIMIT triggering)
- ``portfolio``    — PortfolioManager (positions, P&L)
- ``logger``       — TradeLogger (one row per fill)
- ``runner``       — glues everything into a per-session asyncio task
"""
