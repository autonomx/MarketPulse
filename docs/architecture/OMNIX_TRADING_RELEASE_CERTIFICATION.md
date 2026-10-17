# Omnix Trading Release Certification

This document separates implementation closure, exact-head automated gates, and environment-dependent release evidence. A green unit/typecheck workflow proves automated implementation gates only; it does not replace environment qualification.

## Automated implementation gates

These checks run in the dedicated **Omnix Trading terminal gates** workflow:

- immutable pull-request-head checkout and an explicit `git rev-parse HEAD` equality assertion;
- Trading architecture and backend tests;
- indicator parity fixtures;
- normalized provider/fallback contracts;
- stream duplicate, ordering, gap, and lifecycle logic;
- workspace migration and revision conflict behavior;
- alert cooldown/idempotency/finalized-bar policy;
- scanner bounds, deterministic ranking, cancellation, timeout, and persisted/restart-safe execution once that review item is implemented;
- immutable replay datasets and next-bar backtest sequencing;
- explicit backtest ending cash/position/mark, realized/unrealized P&L, mark-to-market disclosure, deterministic economic-result fingerprint, return, drawdown, win rate, exposure, trades, equity, and logs;
- BlobStore backtest artifact checksum contract;
- paper buying-power and position reservations, semantic idempotency-key validation, high/low limit/stop triggering, account/order/fill/ledger/P&L contracts;
- no-live-broker and no-AI-mutation boundaries;
- read-only research schema, prompt bounds, source metadata, and provider isolation;
- frontend unit tests;
- Trading TypeScript compilation;
- Playwright Trading smoke coverage;
- generated shared-gateway OpenAPI/TypeScript contract parity;
- release/legal/operator structural invariants.

The workflow checks out `${{ github.event.pull_request.head.sha || github.sha }}` rather than a mutable branch name and fails immediately if the checked-out commit differs. The final automated evidence is valid only when the successful run's `head_sha` equals PR #1488's final head exactly.

## Implementation closure rule

The Trading implementation is **code-complete for the reviewed scope** only when all review items are implemented, generated API contracts are committed, and the permanent Trading workflow passes on the final immutable PR head.

A successful run on an earlier SHA does not certify a later head. Likewise, a green workflow does not close a review item that is still knowingly incomplete. In particular, restart-safe scanner execution, persisted incremental progress, cancellation reconciliation, and shared global/provider concurrency budgets must be present before the reviewed implementation can be called code-complete.

## Environment evidence required before release certification

| Evidence | Required procedure | Pass condition | Current status |
|---|---|---|---|
| PostgreSQL migration | Apply all Trading migrations to an empty database and a representative upgraded database. Restart gateway and reload workspace, alerts, scanner definitions/results/progress, replay datasets, backtests, and paper accounts. | No migration errors, no fabricated legacy data, persisted records reload with correct revisions, reservations, fingerprints, and checksums. | Pending environment run |
| PostgreSQL conflict/concurrency | Run concurrent alert evaluation, workspace updates, scanner progress/completion, paper order placement/fills/cancellation, reset/archive, and retry cases against PostgreSQL. | Revision conflicts are deterministic; no duplicate triggers, fills, ledger entries, reservations, progress records, or scanner results. | Pending environment run |
| Provider outage | Interrupt each enabled provider during history, quote, stream, alert, scanner, paper, and research operations. Restore it afterward. | UI identifies the failed provider, fallback is whole-dataset and policy-compliant, workers recover without duplicate state changes. | Pending environment run |
| WebSocket soak | Run the supported live crypto stream for the agreed soak duration with reconnect injection and sequence gaps. | No duplicate bars, no stale subscription after symbol/interval change, bounded reconnects, deterministic REST repair, no resource leak. | Pending environment run |
| Browser accessibility | Exercise Trading with keyboard only, reduced motion, zoom, high contrast, and an accessibility scanner. Review chart alternatives and all dialogs/tabs/drawers. | No critical accessibility violations; focus order and labels are usable; reduced motion is respected. | Pending browser run |
| Browser lifecycle/heap | Repeatedly mount/unmount flexible single- and multi-chart workspaces, switch feeds/timeframes, open/close drawers, run replay, and reconnect streams while measuring heap/listeners/sockets. | No orphan chart, observer, timer, WebSocket, or unbounded heap growth beyond the accepted benchmark. | Pending browser run |
| Chart performance | Run the OTT-0 benchmark procedure on the supported host with representative low/high chart counts and the agreed bar counts/indicators/drawings. Keep the four-chart × 5,000-bar case as a required qualification point. | Meets the accepted first-render, frame, interaction, and memory thresholds documented with raw evidence. | Pending host benchmark |
| Scanner restart and production universe | Run the approved 50-instrument and maximum-allowlist scans against real configured feeds. Restart the gateway mid-run, cancel before and after restart, and run concurrent scans against shared provider budgets. | Incremental progress survives restart, completed instruments are not repeated, cancellation reconciles terminally, global/provider concurrency limits are never exceeded, and results retain exact provider/fingerprint/formula evidence. | Pending implementation and environment run |
| Backtest economics/artifact recovery | Run deterministic duplicate backtests, verify identical economic fingerprints, create a run ending with an open position, verify realized/unrealized reconciliation and final-close mark disclosure, verify BlobStore checksum, then simulate a corrupted/missing artifact. | Economics reconcile exactly; duplicate economic runs hash identically; corruption is detected; relational summaries remain queryable; cleanup/rebuild path succeeds. | Pending environment run |
| Paper reservations | Place competing BUY and SELL orders, retry idempotency keys with equal and unequal payloads, exercise high/low range triggers, cancel/reject orders, and restart the gateway between operations. | Buying power/position quantity cannot be double-reserved, exact retries are harmless, payload mismatches fail, reservations release correctly, and fills/ledger state remain atomic. | Pending environment run |
| Security/legal review | Review active provider terms, redistribution flags, visible attribution, secrets handling, public exposure, and paper/live execution boundaries. | Approved usage scope and attribution; no credential leakage or live-order route. | Pending reviewer approval |
| Rollback rehearsal | Roll back the application version while preserving or explicitly migrating Trading data and artifacts according to the operator plan. | Service returns to the previous supported state without silent data corruption. | Pending environment run |

## Release decision rule

The Trading module is **implementation-complete** when the reviewed work is closed and the permanent workflow passes on the exact final head. It is **release-certified** only when every required environment row above is marked passed with dated evidence and reviewer identity.

Until then:

- PR #1488 remains draft;
- the module must not be represented as production-certified;
- paper simulation and research remain clearly labeled;
- no live brokerage execution may be enabled.

## Evidence record template

For each environment row, attach:

- date and operator;
- exact commit SHA;
- GitHub Actions run ID where applicable;
- environment and versions;
- provider/feed configuration without secrets;
- exact commands or procedure;
- raw logs, screenshots, metrics, or artifact checksums;
- pass/fail conclusion;
- defects and remediation links;
- reviewer approval.
