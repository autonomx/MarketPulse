-- Shared trading evidence/qualification and execution-authority v3 foundations.

-- Binding purpose becomes durable authority. Existing replay/research protections
-- are quarantined once rather than generating an error every reconciliation loop.
ALTER TABLE omnix_trading_paper_protections
    ADD COLUMN IF NOT EXISTS binding_purpose TEXT NOT NULL DEFAULT 'EXECUTION';

UPDATE omnix_trading_paper_protections
   SET binding_purpose = CASE
       WHEN lower(COALESCE(binding_id, '')) LIKE 'replay:%' THEN 'REPLAY'
       WHEN lower(COALESCE(binding_id, '')) LIKE 'research:%' THEN 'RESEARCH'
       WHEN lower(COALESCE(binding_id, '')) LIKE 'live:%' THEN 'LIVE_DATA'
       ELSE 'EXECUTION'
   END;

ALTER TABLE omnix_trading_paper_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_protections_status_check;
ALTER TABLE omnix_trading_paper_protections
    ADD CONSTRAINT omnix_trading_paper_protections_status_check
    CHECK (status IN (
        'pending_entry', 'active', 'exit_submitted',
        'closed', 'cancelled', 'quarantined'
    ));

UPDATE omnix_trading_paper_protections
   SET status = 'quarantined',
       trigger_reason = 'binding_purpose_not_execution',
       revision = revision + 1,
       updated_at = CURRENT_TIMESTAMP
 WHERE status IN ('pending_entry', 'active', 'exit_submitted')
   AND binding_purpose <> 'EXECUTION';

ALTER TABLE omnix_trading_paper_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_protections_binding_purpose_check;
ALTER TABLE omnix_trading_paper_protections
    ADD CONSTRAINT omnix_trading_paper_protections_binding_purpose_check
    CHECK (binding_purpose IN ('LIVE_DATA', 'EXECUTION', 'REPLAY', 'RESEARCH'));

ALTER TABLE omnix_trading_paper_protections
    DROP CONSTRAINT IF EXISTS omnix_trading_paper_protections_execution_binding_check;
ALTER TABLE omnix_trading_paper_protections
    ADD CONSTRAINT omnix_trading_paper_protections_execution_binding_check
    CHECK (
        status NOT IN ('pending_entry', 'active', 'exit_submitted')
        OR binding_purpose = 'EXECUTION'
    );

CREATE TABLE IF NOT EXISTS omnix_trading_trigger_plans (
    workspace_id TEXT NOT NULL,
    trigger_plan_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    arm_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'ARMED', 'TRIGGERED', 'EXECUTION_CHECK', 'FILLED',
            'REJECTED', 'EXPIRED', 'INVALIDATED',
            'TRIGGER_ORDER_UNRESOLVED'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    trigger_payload JSONB NOT NULL,
    geometry_payload JSONB NOT NULL,
    required_certificate_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    origin_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    transition_reason TEXT,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, trigger_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_trigger_plans_active
    ON omnix_trading_trigger_plans
    (workspace_id, strategy_id, status, instrument_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS omnix_trading_session_evidence_manifests (
    workspace_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    session_date DATE NOT NULL,
    strategy_id TEXT NOT NULL,
    reconciliation_state TEXT NOT NULL CHECK (
        reconciliation_state IN (
            'PENDING_DATA', 'RETRYING', 'FINAL', 'PERMANENTLY_UNSCORABLE'
        )
    ),
    payload JSONB NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_retry_at TIMESTAMPTZ,
    frozen_at TIMESTAMPTZ NOT NULL,
    finalized_at TIMESTAMPTZ,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, manifest_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_omnix_trading_session_manifest_strategy_day
    ON omnix_trading_session_evidence_manifests
    (workspace_id, strategy_id, session_date);
