# Omnix Trading Operations Runbook

## Runtime ownership

Trading runs inside the shared Omnix gateway and web application. Do not start a separate prototype FastAPI or Vite workstation.

Gateway lifecycle services:

- Trading market-data REST and WebSocket routes
- server alert monitor
- bounded scanner manager
- replay/backtest routes
- paper simulation monitor
- deterministic strategy monitor
- read-only AI research route
- catalyst evidence and shadow classification routes
- shadow-only statistical-model training/scoring routes

## Environment controls

### Alert monitor

- `OMNIX_TRADING_ALERT_MONITOR=0` disables production alert monitoring.
- `OMNIX_TRADING_ALERT_INTERVAL_SECONDS` controls the polling interval with a five-second minimum.
- In `legacy_test` mode, enable only with `OMNIX_TRADING_ALERT_MONITOR_IN_TESTS=1`.

### Paper monitor

- `OMNIX_TRADING_PAPER_MONITOR=0` disables paper-order monitoring.
- `OMNIX_TRADING_PAPER_INTERVAL_SECONDS` controls the polling interval with a five-second minimum.
- In `legacy_test` mode, enable only with `OMNIX_TRADING_PAPER_MONITOR_IN_TESTS=1`.

The paper monitor owns market observations and manual paper stop/target protection. Browser observations are not execution authority.

### Strategy monitor

- `OMNIX_TRADING_STRATEGY_MONITOR=0` disables deterministic strategy monitoring.
- `OMNIX_TRADING_STRATEGY_INTERVAL_SECONDS` controls the polling interval with a five-second minimum.
- In `legacy_test` mode, enable only with `OMNIX_TRADING_STRATEGY_MONITOR_IN_TESTS=1`.
- Strategy runtime modes are `off`, `shadow`, and `auto_paper`. There is no live-broker mode.
- Live evaluation stops once the session settles, 30 minutes after the regular close (`regular_session_settled`; 13:30 ET on early-close days): the session's bars are final, so every later pass would repeat the last one, provider requests included. Protections are still reconciled every cycle, and skipped passes log `strategy_cycle_skipped` with reason `session_settled`. The deep-recovery and execution-observation monitors stop at the same point; post-close research (interday discovery, AI shadow labelling, overnight shadow) is unaffected.
- Every pass logs `monitor_pass_timing`: seconds per strategy, configurations left unfinished, and the bar-recovery work done meanwhile (calls and seconds per stage: primary fetch, saved Yahoo evidence, Yahoo exact repair, IBKR, IEX, and tapes reused). A pass starts no new configuration after 80% of its timeout; configurations it did not finish, including any interrupted by the timeout, run first in the next pass.
- Strategies share each stock's repaired bar tape within a clock minute (`TradingMarketDataService.recovered_bars`): a live copy that already holds the latest expected bar, or was taken at least 20 s into the minute, is reused instead of running the provider chain again. Before its candidate loop, each pass warms the tape four stocks at a time. Replays and explicit knowledge cutoffs never reuse a tape. The service's diagnostics report the per-stage totals under `bar_recovery`.

### Provider configuration

Market-data providers and the optional research model use Omnix provider configuration. Do not place credentials, tokens, or base URLs in Trading workspace records, scanner definitions, research requests, or browser storage.

Execution-grade data is a stricter contract than chart/research data. An execution observation must pass source-time freshness, session, bid/ask, and spread policy before it can drive a paper fill. The Yahoo equity adapter is deliberately research/diagnostic only and returns `PROVIDER_NOT_EXECUTION_GRADE`; therefore US-equity AUTO PAPER remains fail-closed until an approved execution-grade feed is configured. Never remove that rejection merely to make a simulation fill.

## Provider outage procedure

1. Open the Trading **Data** tab and record provider/stream diagnostics.
2. Confirm the selected feed, official/unofficial status, usage scope, and terms reference in the Trading footer.
3. Disable affected background monitors if repeated failures could consume rate limits.
4. Preserve the exact instrument, requested binding, resolved binding, provider, dataset fingerprint, source time, and error.
5. Do not merge partial data from two providers. A fallback replaces the complete requested dataset.
6. Restore the provider or select an allowed compatible binding.
7. Re-enable monitors and verify that retries do not duplicate alert triggers, paper fills, ledger entries, scanner results, strategy events, or protections.
8. Attach the outage/recovery evidence to the release certification record.

Missing, stale, cached, fallback, unknown-session, bookless, or over-wide execution data must produce no paper fill. `reference_price` is reservation evidence only and is never a market observation fallback.

## Streaming recovery

- Symbol, interval, or binding changes must close the prior subscription before opening the replacement.
- Sequence gaps require deterministic REST repair before live updates continue.
- Repeated duplicate or out-of-order events should be recorded with binding and provider sequence metadata.
- Stop the Trading gateway if sockets or reconnect attempts become unbounded; do not leave a degraded reconnect loop unattended.

## PostgreSQL migrations and rollback

Trading migrations are under `src/app/persistence/migrations` and must be applied through the existing Omnix migration process.

Before upgrade:

1. Back up the database and BlobStore root.
2. Record the application commit SHA and latest applied migration.
3. Confirm sufficient disk space for datasets, scanner results, backtest evidence, paper ledger history, strategy events, evidence records, model artifacts, and artifacts.

After upgrade:

1. Verify Trading tables and constraints.
2. Restart the gateway.
3. Reload a representative workspace, alert, scanner, dataset, backtest, paper account, frozen universe, and strategy configuration.
4. Verify backtest artifact checksums.
5. Confirm monitor diagnostics and absence of duplicate state changes.
6. Confirm strategy/model migrations preserve `shadow_only` and paper-only constraints.

Rollback must preserve Trading records and BlobStore artifacts unless a reviewed data migration explicitly removes them. Do not invent legacy signal/fill indices; pre-index backtests retain NULL sequencing evidence.

## BlobStore artifacts

Complete legacy backtest result JSON is stored through `LocalBlobStore`. Relational PostgreSQL rows retain queryable summaries, trades, equity points, and logs. The gap-pullback portfolio backtester additionally freezes its candidate universe and per-symbol bars in a deterministic session dataset fingerprint.

Artifact metadata includes:

- storage provider
- storage key
- SHA-256 checksum
- byte size

On read, checksum mismatch is an integrity failure. Do not serve the corrupted artifact as valid evidence.

### Corrupt or missing artifact

1. Record the run ID, storage key, expected checksum, and error.
2. Keep relational summaries and evidence read-only.
3. Quarantine the corrupt file; do not overwrite it without preserving evidence.
4. Re-run the backtest from the same frozen dataset and strategy/execution policy.
5. Compare the new deterministic result to relational evidence.
6. Link the replacement run; do not silently mutate the original run identity.

### Orphan artifact cleanup

A BlobStore write can succeed before a database transaction fails. The runtime attempts to delete artifacts it created during a failed save. Periodic cleanup may remove unreferenced Trading artifact keys only after comparing them with PostgreSQL run metadata and preserving an audit log.

## Cache and disk cleanup

- Never delete active PostgreSQL authority records as a cache-clearing shortcut.
- Cached/fallback market data must remain distinguishable from live/polled data through provenance.
- Scanner, strategy-event, evidence, model-score, and backtest history are evidence, not disposable browser cache.
- Before deleting local artifacts, verify that no run metadata references their storage key.

## Scanner operations

- Maximum allowlist: 200 unique instruments.
- Maximum history: 500 bars per instrument.
- Maximum concurrency: 8.
- Maximum request timeout: 30 seconds.
- Maximum run timeout: 300 seconds.
- Default formulas are versioned as `omnix-indicators-v2`.

Cancel scans that exceed provider policy or operational expectations. Do not broaden the legacy scanner universe dynamically beyond the stored allowlist. The gapper-universe import is a separate point-in-time dataset and must not be reconstructed from eventual winners after the session.

## Paper simulation operations

Paper simulation is not brokerage execution.

- Market, Limit, and Stop only.
- Long and short accounting use the same relational paper ledger; automated `gap_pullback_v1` entries are long-only.
- Reset and archive require the current account revision.
- Reset deletes simulated state, records a new explicit deposit, cancels protections, and turns related strategy automation off.
- Archive disables the account, releases reservations, cancels open orders/protections, and turns related strategy automation off without deleting evidence.
- Fill, cash, position, order, and ledger changes commit together under row locks.
- `paper-execution-v2` applies deterministic spread-side pricing, slippage, stop gap-through behavior, observation latency, max volume participation, partial fills, stale-data rejection, and halt rejection.
- Manual take-profit/stop-loss protection is PostgreSQL authority. Browser `localStorage` is not a protection source of truth.

If duplicate fills or ledger entries are suspected, stop the paper monitor and inspect idempotency keys before resuming.

## Gap-pullback automation

The thesis is **buy the first failed sell-off**, not the first sell-off. `gap_pullback_v1` is a deterministic state machine built around confirmed opening structure: opening impulse → confirmed first pullback low (L1) → confirmed bounce high (B1) → confirmed higher second low (L2) → regular-session VWAP reclaim → B1 break → breakout-volume confirmation.

Operational sequence:

1. Before trading, freeze a point-in-time daily gapper universe through **Trading → Strategies → Freeze point-in-time gapper universe** or `POST /api/trading/strategies/universes/freeze`. Preserve every candidate that qualified at that time, including eventual fades/failures.
2. Candidate evidence should include previous close, premarket price/gap, premarket volume and dollar volume, time-of-day RVOL, spread when available, market cap/float when available, catalyst evidence IDs, dilution flags, and discovery rank.
3. Start new configurations in `shadow`. Inspect candidate state/reason codes and rejected candidates before enabling `auto_paper`.
4. Run the frozen multi-symbol strategy backtest with `POST /api/trading/strategies/backtest/gap-pullback`. Entry is next-bar, and the backtester uses the same `paper_fill_decision` engine as the paper monitor.
5. Validate prefix invariance/causality: a pivot is unknown until its configured right-side bars exist. Future bars must never alter an already evaluated prefix.
6. `auto_paper` may submit only to the existing paper repository. It additionally requires an execution-eligible observation and server risk approval.
7. Risk gates include account-risk sizing, daily loss, aggregate open risk, max positions, max trades/day, one trade/symbol/day, max notional, spread, entry window, force-flat time, and kill switch.
8. Every automated entry receives persisted strategy stop/target protection. At force-flat time the strategy submits a paper exit if execution data is eligible.

If AUTO PAPER is unexpectedly active, set the strategy kill switch and mode to `off`, or set `OMNIX_TRADING_STRATEGY_MONITOR=0` and restart the gateway. Do not use data/research model failures as a reason to bypass execution eligibility.

## Catalyst and AI shadow operations

Catalyst context is an evidence pipeline, not an LLM opinion field.

- Capture timestamped immutable evidence with source type (`sec`, `company`, `news`, `manual`), source locator, publication/capture timestamps, text hash, deterministic facts, and dilution evidence.
- Dilution flags such as ATM, warrants, convertible securities, shelf/resale registration, and equity lines are extracted from supplied evidence and retained with the immutable fingerprint.
- `classify-shadow` can ask the configured model to classify only the supplied evidence IDs. It must cite exactly those IDs and returns `shadow_only=true`.
- No AI output can authorize an order. Two research paths do reach `strategy_monitor.py`, and both are bounded:
  - The intraday LLM (`intraday_llm_enabled`) runs as a research annotation beside the strategy cycle. Proposals never wait for it, and its assessments are recorded as `intraday_llm` events only.
  - For gap pullback `1.2.0`, the reviewed research policy (HTR) can block an entry through its hard gates. It changes the setup quality score only when the strategy sets `research_score_adjustment_enabled`. That setting is off by default. Changing it is a strategy update, which needs `trading:strategies:admin` and is audited. Every `research_policy` event records the proposed adjustment and whether it was applied.

Every research model call (intraday LLM, AI shadow v2/v3) counts against a daily budget, `OMNIX_TRADING_AI_DAILY_CALL_BUDGET` (default 400 calls per Eastern-time day, shared by all processes through the evidence session counters; 0 turns it off). The intraday LLM, which live strategy passes read, may use the whole budget; the AI shadow arms (and any analyzer not listed) stop at 60%. A call over its tier's share fails with `trading_ai_budget_exhausted` before reaching the provider. The Claude CLI receives each analyzer's JSON schema (`--json-schema`), so its replies are validated against the contract instead of guessed from the prompt.

Use only sources whose access, retention, and redistribution terms are appropriate for the configured environment. Source acquisition adapters may be added separately; the evidence boundary must remain immutable and timestamped.

## Statistical model operations

The bounce model label is `P(+2R before -1R within 90 minutes)`. Same-bar stop/target ambiguity resolves pessimistically to the stop.

- The transparent baseline score remains shadow-only.
- `POST /api/trading/models/bounce/train` fits a standardized logistic regression from explicit labeled examples and persists coefficients, intercept, feature means/scales, training metadata, log loss, and a SHA-256 artifact fingerprint.
- A model version is immutable: reusing a version with a different fingerprint is rejected.
- `POST /api/trading/models/bounce/score-shadow` persists a shadow score for later out-of-sample evaluation.
- Model artifacts and scores are deliberately absent from the deterministic strategy monitor. Promotion into any strategy gate requires a separate reviewed change and out-of-sample evidence.

Do not interpret training fit or a paper backtest as proof of future profitability.

## Research operations

Research is optional and read-only.

- It uses the configured Omnix provider registry.
- It accepts no credentials.
- It uses at most 200 finalized bars and a bounded context.
- Invalid model JSON is rejected; no generic narrative fallback is saved.
- It cannot place orders, create alerts, or mutate scanner/backtest/paper state.

Provider/model errors should not block charting, alerts, scanner use, replay, or paper simulation.

## Incident record

For Trading incidents record:

- commit SHA and environment;
- workspace/user scope;
- canonical instrument and binding;
- provider and policy scope;
- source and evaluation timestamps;
- frozen universe/dataset fingerprint or artifact checksum;
- strategy ID/version/mode and candidate reason code when relevant;
- model/evidence fingerprints when relevant;
- relevant revision/idempotency key;
- diagnostics output;
- remediation and validation result.
