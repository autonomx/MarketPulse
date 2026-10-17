# Omnix Trading Security and Legal Boundaries

## Product boundary

Omnix Trading is a research, charting, alerting, replay/backtesting, scanning, and paper-simulation module. It does not provide live brokerage execution and does not authorize an AI model to mutate financial state.

## Secrets

- Market-data and AI provider credentials remain in existing Omnix provider settings.
- Trading REST requests, workspaces, scanner definitions, alert definitions, replay datasets, paper orders, and research requests must not contain API keys, secrets, or provider base URLs.
- Logs and diagnostics may identify a provider, binding, model, policy, source time, dataset fingerprint, or artifact checksum, but must not print credentials.

## Market-data rights

Provider policy is part of runtime configuration and UI disclosure:

- usage scope
- redistribution permission
- official/unofficial API status
- delay/realtime scope
- supported asset classes and intervals
- terms reference

A provider being technically accessible does not grant redistribution rights. The Trading footer displays the active policy. Whole-dataset fallback is required; partial provider mixing is not permitted.

## Attribution and notices

The Trading workspace visibly displays **Charts powered by TradingView Lightweight Charts™** and links to the project. Root `THIRD_PARTY_NOTICES.md` records Lightweight Charts and the MIT-licensed `tradingview-mcp` prototype notice.

## Research isolation

The AI research route:

- resolves through the existing Omnix provider registry;
- accepts no credentials or arbitrary provider configuration;
- receives only bounded normalized market context;
- requires strict JSON output;
- attaches source/provider/model evidence server-side;
- rejects execution-shaped extra fields;
- has no dependency on alert creation, backtest execution, paper fills, paper orders, or live orders.

Every result is labeled read-only and includes a not-financial-advice/no-order disclaimer.

## Paper simulation isolation

Paper state is stored in dedicated PostgreSQL tables. Paper routes use `/api/trading/paper/...`. There is no broker credential model, live-order endpoint, or brokerage client in the Trading subsystem.

Paper fills, balances, positions, order status, and ledger entries are transactional and idempotent. Reset/archive are explicit revisioned operations.

## Public exposure

Before exposing Trading beyond its approved local/internal scope:

1. review every enabled provider's redistribution terms;
2. replace personal/local feeds with appropriately licensed feeds;
3. review authentication, authorization, rate limiting, CORS, audit logging, and data retention;
4. repeat the security/legal release certification;
5. do not infer that the current personal/local provider policy permits public or commercial redistribution.

## Deferred security-sensitive features

The following require separate threat modeling and legal review:

- live brokerage execution;
- AI-autonomous trading;
- shared collaborative workspaces;
- public market-data redistribution;
- user-supplied provider endpoints;
- webhook-triggered financial actions;
- margin, shorting, derivatives, or multi-currency custody/accounting.
