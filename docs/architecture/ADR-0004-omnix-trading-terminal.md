# ADR-0004: Native Omnix Trading Terminal

**Status:** Accepted for feasibility implementation  
**Date:** 2026-08-05  
**Roadmap:** `docs/plans/omnix_trading_terminal_roadmap.md`

## Context

Omnix needs a TradingView-style research workspace with multiple linked charts, real drawings, indicators, live crypto data, saved workspaces, and later alerts, replay, backtesting, paper simulation, and bounded AI research. The existing `autonomxDeveloper/tradingview-mcp` repository validates useful data and UI ideas, but its MCP server, standalone FastAPI application, standalone Vite application, mock drawing overlays, and direct model calls do not fit Omnix ownership boundaries.

## Decision

1. Trading is a native `/trading` Omnix module using the existing React shell, TanStack Router, Mantine appearance system, Query client, Zustand conventions, generated OpenAPI types, FastAPI gateway, diagnostics, jobs, PostgreSQL authority, and BlobStore.
2. The prototype is a code donor and reference only. Production code has no MCP runtime dependency and no standalone Trading server or shell.
3. TradingView Lightweight Charts is the renderer candidate, not an interaction framework. Omnix owns chart lifecycle, synchronization, drawings, hit testing, keyboard behavior, persistence, streaming recovery, and accessibility.
4. A disposable spike must prove four charts with 5,000 bars each, different-interval synchronization, an RSI pane, selectable and resizable time/price drawings, resize/fullscreen behavior, shared live-stream handling, exact gap recovery, and lifecycle cleanup.
5. The spike is a go/no-go gate. Production `/trading` routing begins only after benchmark evidence and an explicit architecture decision accept the renderer and drawing approach.
6. PostgreSQL is authoritative for workspaces, watchlists, drawings, presets, alerts, backtest metadata, and paper state. The provider cache is a bounded in-process cache only (WP-8.3; it no longer persists to `resources/cache/trading/`).
7. Canonical instrument identity is independent of provider/feed identity. Instrument-owned state survives provider changes and fallback.
8. Provider legality and intended usage are executable adapter capabilities. No dataset silently splices multiple providers.
9. The first qualified release is crypto-only and uses Binance spot data. Equities begin only after Charting Beta qualification.
10. Live brokerage execution is outside this program.

## Spike exit policy

- Reusable adapter code is retained only after this ADR is amended with measured acceptance evidence.
- Disposable UI and benchmark scaffolding is deleted or remains under `features/trading/experimental` and is never production-routed.
- Benchmark evidence records environment, method, dataset size, results, and known limitations.
- A failed spike blocks production `/trading` work and triggers renderer or interaction-model reassessment.
- Temporary spike contracts cannot become production contracts without OTT-1 OpenAPI review.

## Consequences

The application receives a single coherent Trading owner and can add data providers without detaching watchlists, drawings, alerts, or positions. Omnix must implement substantial drawing and synchronization behavior itself. This increases initial engineering work but avoids presenting decorative controls as functional and prevents provider-specific payloads from leaking into the UI.

## Rejected alternatives

- Embedding the `tradingview-mcp` workstation.
- Running a second frontend or FastAPI application.
- Treating display symbols as canonical identity.
- Mutable JSON as production authority.
- Browser-only alerts or paper ledgers.
- Implementing Pine Script compatibility in the initial program.
