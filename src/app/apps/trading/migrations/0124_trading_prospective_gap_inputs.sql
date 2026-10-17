-- omnix-migration: phase=expand transactional=true
-- WP-8.3: prospective-gap decision inputs live in PostgreSQL, not in local or
-- remote files read on the decision path. A premarket handoff is imported
-- once per session with its provenance (the exact content, its SHA-256 and
-- where it came from) and is immutable after import. Climatology states are
-- versioned by the last session they cover. Market evidence is shared by
-- every workspace, so neither table is per workspace.
CREATE TABLE IF NOT EXISTS omnix_trading_premarket_handoffs (
    session_date DATE PRIMARY KEY,
    handoff_version TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    source TEXT NOT NULL,
    imported_by TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS omnix_trading_climatology_states (
    through_session DATE PRIMARY KEY,
    version TEXT NOT NULL,
    observation_count INTEGER NOT NULL CHECK (observation_count >= 0),
    positive_count INTEGER NOT NULL CHECK (positive_count >= 0 AND positive_count <= observation_count),
    probability NUMERIC NOT NULL CHECK (probability >= 0 AND probability <= 1),
    source TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed: the state previously committed as
-- resources/trading/prospective_gap_state/climatology.json.
INSERT INTO omnix_trading_climatology_states (
    through_session, version, observation_count, positive_count, probability, source
) VALUES (
    '2026-09-23', 'prospective-gap-climatology-state-v1', 50, 19, 0.38,
    'seed:resources/trading/prospective_gap_state/climatology.json'
)
ON CONFLICT (through_session) DO NOTHING;
