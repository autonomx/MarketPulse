CREATE TABLE IF NOT EXISTS omnix_trading_strategy_configs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    strategy_kind TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('off', 'shadow', 'auto_paper')),
    active_universe_id TEXT,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, strategy_id),
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_configs_active
    ON omnix_trading_strategy_configs (workspace_id, enabled, mode, updated_at);

CREATE TABLE IF NOT EXISTS omnix_trading_strategy_runs (
    workspace_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (workspace_id, strategy_id, run_id),
    UNIQUE (workspace_id, strategy_id, run_key),
    FOREIGN KEY (workspace_id, strategy_id)
        REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_trading_strategy_events (
    workspace_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    run_id TEXT,
    instrument_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT NOT NULL,
    reason_code TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, strategy_id, event_id),
    UNIQUE (workspace_id, strategy_id, idempotency_key),
    FOREIGN KEY (workspace_id, strategy_id)
        REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_events_recent
    ON omnix_trading_strategy_events (workspace_id, strategy_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_strategy_protections (
    workspace_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    protection_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    entry_order_id TEXT NOT NULL,
    exit_order_id TEXT,
    stop_price NUMERIC NOT NULL CHECK (stop_price > 0),
    target_price NUMERIC NOT NULL CHECK (target_price > 0),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    status TEXT NOT NULL CHECK (status IN ('pending_entry', 'active', 'exit_submitted', 'closed', 'cancelled')),
    trigger_reason TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, strategy_id, protection_id),
    UNIQUE (workspace_id, strategy_id, entry_order_id),
    FOREIGN KEY (workspace_id, strategy_id)
        REFERENCES omnix_trading_strategy_configs(workspace_id, strategy_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, account_id)
        REFERENCES omnix_trading_paper_accounts(workspace_id, account_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_strategy_protections_active
    ON omnix_trading_strategy_protections (workspace_id, strategy_id, status, instrument_id);

CREATE TABLE IF NOT EXISTS omnix_trading_gapper_universes (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    universe_id TEXT NOT NULL,
    session_date DATE NOT NULL,
    evaluation_time TIMESTAMPTZ NOT NULL,
    discovery_source TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    candidates JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, universe_id),
    UNIQUE (workspace_id, source_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_gapper_universes_session
    ON omnix_trading_gapper_universes (workspace_id, session_date DESC, evaluation_time DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_catalyst_evidence (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('sec', 'company', 'news', 'manual')),
    source_locator TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    headline TEXT,
    content TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    facts JSONB NOT NULL DEFAULT '{}'::jsonb,
    dilution_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    immutable_fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, evidence_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_catalyst_evidence_instrument
    ON omnix_trading_catalyst_evidence (workspace_id, instrument_id, published_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_model_scores (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    score_id TEXT NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    probability NUMERIC NOT NULL CHECK (probability BETWEEN 0 AND 1),
    features JSONB NOT NULL,
    label_definition TEXT NOT NULL,
    shadow_only BOOLEAN NOT NULL DEFAULT TRUE,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, score_id),
    UNIQUE (workspace_id, fingerprint)
);
