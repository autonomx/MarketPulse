# Omnix Trading Prototype Migration Inventory

Reference: `autonomxDeveloper/tradingview-mcp`

| Prototype area | Decision | Omnix destination | Notes |
|---|---|---|---|
| Yahoo/yfinance chart service | Rewrite with attribution | `src/app/apps/trading/providers/yahoo.py` | Retain normalization and fallback lessons; add provider policy, canonical instruments, sessions, adjustments, cancellation, and typed errors. Not part of Charting Beta. |
| Stooq daily fallback | Rewrite with attribution | `src/app/apps/trading/providers/stooq.py` | Whole-dataset fallback only. Never splice with Yahoo. |
| Binance candle pagination | Migrate selectively with attribution | `src/app/apps/trading/providers/binance.py` | Preserve pagination tests; separate REST and WebSocket bindings from canonical instrument identity. |
| Coinbase/Kraken adapters | Rewrite later | provider modules | Add only after crypto-only Beta qualification. |
| Market-data file cache | Reference only | `src/app/apps/trading/cache.py` | New cache is bounded, disposable and in-process (no disk copy since WP-8.3); no user authority. |
| Symbol suffix inference | Reject as authority | instrument resolver | `BTCUSDT` is an alias, not a canonical ID. |
| Workstation FastAPI app | Reject | Omnix gateway | No second server or static app owner. |
| MCP tools/server | Reject | none | No MCP runtime dependency. |
| Direct LM Studio calls | Reject | Omnix provider registry | Optional read-only research arrives in OTT-14. |
| React AppShell | Reference only | `TradingWorkspace` | Rebuild with Omnix shell and design primitives. |
| ChartWorkspace Lightweight Charts usage | Rewrite | chart adapter | Prototype lifecycle and chart-style fallbacks are insufficient. |
| Zustand UI store | Reference only | trading store | Separate server authority, draft interaction state, and crash recovery. |
| Static drawing overlays | Reject | drawing engine | Must use time/price coordinates, hit testing, handles, commands, and persistence. |
| Watchlist/layout JSON services | Reject as authority | PostgreSQL repositories | Use revisioned module records initially. |
| Paper-trading service | Reference only | dedicated relational schema | Transactional ledger is later scope. |
| Backtest service | Reference only | deterministic engine | Requires frozen dataset fingerprints and versioned indicator formulas. |
| Existing tests/fixtures | Migrate selectively | trading tests | Provider parsing fixtures and pagination cases are reusable; mock-only UI tests are not. |

## Attribution policy

Substantial copied MIT-licensed sections retain the original copyright and permission notice in `THIRD_PARTY_NOTICES.md` or an adjacent source header. Ideas, response shapes, and rewritten algorithms are documented in commit messages and test provenance where appropriate.

## Prohibited migration outcomes

- imports from `tradingview_mcp` in production Omnix code;
- provider-specific payloads exposed to the frontend;
- decorative-only chart tools represented as functional;
- mutable files used as authoritative watchlist, workspace, drawing, alert, or paper state;
- a second theme, router, query client, application header, or server;
- live broker execution routes.
