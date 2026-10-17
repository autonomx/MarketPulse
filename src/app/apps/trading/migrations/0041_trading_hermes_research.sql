CREATE TABLE IF NOT EXISTS omnix_trading_issuer_identities (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    identity_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT,
    legal_name TEXT,
    cik TEXT,
    source TEXT NOT NULL,
    source_available_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, identity_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_issuer_identity_asof
    ON omnix_trading_issuer_identities (workspace_id, instrument_id, omnix_known_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_research_evidence (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    issuer_identity_id TEXT,
    evidence_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_authority_tier INTEGER NOT NULL CHECK (source_authority_tier BETWEEN 1 AND 4),
    source_published_at TIMESTAMPTZ,
    source_available_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    title TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, evidence_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_evidence_asof
    ON omnix_trading_research_evidence (workspace_id, instrument_id, omnix_known_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_research_actions (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    step INTEGER NOT NULL CHECK (step >= 0),
    operation TEXT NOT NULL,
    args JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason TEXT,
    status TEXT NOT NULL,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_code TEXT,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, action_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_actions_trace
    ON omnix_trading_research_actions (workspace_id, trace_id, step);

CREATE TABLE IF NOT EXISTS omnix_trading_research_reports (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    report_id TEXT NOT NULL,
    report_version INTEGER NOT NULL CHECK (report_version >= 1),
    contract_version TEXT NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    research_started_at TIMESTAMPTZ NOT NULL,
    research_completed_at TIMESTAMPTZ,
    evidence_cutoff_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    catalyst_status TEXT NOT NULL,
    supply_status TEXT NOT NULL,
    research_status TEXT NOT NULL,
    coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
    unresolved_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    hermes_trace_id TEXT,
    planner_backend TEXT NOT NULL,
    stop_reason TEXT,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, report_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_reports_asof
    ON omnix_trading_research_reports (workspace_id, instrument_id, omnix_known_at DESC, report_version DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_supply_facts (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    fact_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    supply_type TEXT NOT NULL,
    status TEXT NOT NULL,
    shares NUMERIC,
    remaining_capacity_usd NUMERIC,
    strike_price NUMERIC,
    exercise_status TEXT,
    registration_status TEXT,
    effective_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    source_evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    resolution_status TEXT NOT NULL,
    confidence NUMERIC NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    generated_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, fact_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_supply_facts_asof
    ON omnix_trading_supply_facts (workspace_id, instrument_id, omnix_known_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_fact_sets (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    fact_set_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    report_id TEXT,
    generated_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    catalyst JSONB NOT NULL,
    supply JSONB NOT NULL DEFAULT '[]'::jsonb,
    supply_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    completeness JSONB NOT NULL DEFAULT '{}'::jsonb,
    unresolved_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, fact_set_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_fact_sets_asof
    ON omnix_trading_fact_sets (workspace_id, instrument_id, omnix_known_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_research_features (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    feature_id TEXT NOT NULL,
    projection_version TEXT NOT NULL,
    research_policy_version TEXT NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    fact_set_id TEXT NOT NULL,
    decision_at TIMESTAMPTZ NOT NULL,
    omnix_known_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    features JSONB NOT NULL,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, feature_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_features_asof
    ON omnix_trading_research_features (workspace_id, instrument_id, decision_at DESC, omnix_known_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_research_outcomes (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    outcome_id TEXT NOT NULL,
    session_date DATE NOT NULL,
    strategy_id TEXT,
    instrument_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    research_policy_version TEXT NOT NULL,
    feature_projection_version TEXT NOT NULL,
    market_fidelity TEXT NOT NULL,
    research_fidelity TEXT NOT NULL,
    research_status TEXT NOT NULL,
    features JSONB NOT NULL,
    strategy_state TEXT,
    rejection_reason TEXT,
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    mfe_r NUMERIC,
    mae_r NUMERIC,
    r_result NUMERIC,
    two_r_before_minus_one_r BOOLEAN,
    time_to_mfe_minutes NUMERIC,
    time_to_stop_minutes NUMERIC,
    data_quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, outcome_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_omnix_trading_research_outcomes_strategy
    ON omnix_trading_research_outcomes (workspace_id, strategy_id, session_date DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_research_validation_reports (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    validation_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    sample_size INTEGER NOT NULL CHECK (sample_size >= 0),
    exact_sample_size INTEGER NOT NULL CHECK (exact_sample_size >= 0),
    feature_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    promotion_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    immutable_fingerprint TEXT NOT NULL,
    PRIMARY KEY (workspace_id, validation_id),
    UNIQUE (workspace_id, immutable_fingerprint)
);
