CREATE TABLE IF NOT EXISTS omnix_runtime_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL CHECK (node_type IN ('gateway', 'worker', 'event_consumer')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'draining', 'stale', 'stopped')),
    capabilities TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    resource_classes TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    software_version TEXT NOT NULL,
    process_id TEXT,
    host_fingerprint TEXT,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_omnix_runtime_nodes_live
    ON omnix_runtime_nodes (node_type, status, lease_expires_at, id);

CREATE TABLE IF NOT EXISTS omnix_runtime_failure_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scenario TEXT NOT NULL,
    node_id TEXT,
    aggregate_type TEXT,
    aggregate_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('recovered', 'blocked', 'failed')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_runtime_failure_scenario
    ON omnix_runtime_failure_evidence (scenario, created_at DESC, id DESC);
