ALTER TABLE omnix_memory_v2_authority_streams
    ADD COLUMN IF NOT EXISTS governance_revision BIGINT NOT NULL DEFAULT 0
        CHECK (governance_revision >= 0);

ALTER TABLE omnix_memory_v2_observations
    ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK (sensitivity IN ('normal', 'sensitive', 'secret'));

ALTER TABLE omnix_memory_v2_grants
    ADD COLUMN IF NOT EXISTS revision BIGINT NOT NULL DEFAULT 1
        CHECK (revision >= 1);

ALTER TABLE omnix_memory_v2_search_index_state
    ADD COLUMN IF NOT EXISTS index_derived_revision BIGINT NOT NULL DEFAULT 0
        CHECK (index_derived_revision >= 0);

ALTER TABLE omnix_memory_v2_search_index_entries
    ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'normal'
        CHECK (sensitivity IN ('normal', 'sensitive', 'secret'));
ALTER TABLE omnix_memory_v2_search_index_entries
    ADD COLUMN IF NOT EXISTS trust_class TEXT NOT NULL DEFAULT 'assistant_inference'
        CHECK (trust_class IN (
            'user_explicit', 'system_trusted', 'assistant_inference',
            'external_untrusted', 'imported_unverified'
        ));
ALTER TABLE omnix_memory_v2_search_index_entries
    ADD COLUMN IF NOT EXISTS effective_visibility JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE omnix_memory_v2_search_index_entries
    ADD COLUMN IF NOT EXISTS policy_digest TEXT NOT NULL DEFAULT '';

ALTER TABLE omnix_memory_v2_consolidation_receipts
    ADD COLUMN IF NOT EXISTS decision_set_id TEXT;
ALTER TABLE omnix_memory_v2_consolidation_receipts
    ADD COLUMN IF NOT EXISTS resulting_derived_revision BIGINT
        CHECK (resulting_derived_revision IS NULL OR resulting_derived_revision >= 1);

ALTER TABLE omnix_memory_v2_cutover_readiness_receipts
    ADD COLUMN IF NOT EXISTS governance_revision BIGINT NOT NULL DEFAULT 0
        CHECK (governance_revision >= 0);
ALTER TABLE omnix_memory_v2_cutover_readiness_receipts
    ADD COLUMN IF NOT EXISTS derived_revision BIGINT NOT NULL DEFAULT 0
        CHECK (derived_revision >= 0);
ALTER TABLE omnix_memory_v2_cutover_readiness_receipts
    ADD COLUMN IF NOT EXISTS derived_source_observation_watermark BIGINT NOT NULL DEFAULT 0
        CHECK (derived_source_observation_watermark >= 0);
ALTER TABLE omnix_memory_v2_cutover_readiness_receipts
    ADD COLUMN IF NOT EXISTS derived_source_governance_revision BIGINT NOT NULL DEFAULT 0
        CHECK (derived_source_governance_revision >= 0);
ALTER TABLE omnix_memory_v2_cutover_readiness_receipts
    ADD COLUMN IF NOT EXISTS index_derived_revision BIGINT NOT NULL DEFAULT 0
        CHECK (index_derived_revision >= 0);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_consolidation_decision_sets (
    decision_set_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    input_observation_from BIGINT NOT NULL CHECK (input_observation_from >= 1),
    input_observation_through BIGINT NOT NULL CHECK (input_observation_through >= input_observation_from),
    previous_derived_revision BIGINT NOT NULL CHECK (previous_derived_revision >= 0),
    source_governance_revision BIGINT NOT NULL CHECK (source_governance_revision >= 0),
    normalized_proposals JSONB NOT NULL DEFAULT '[]'::jsonb,
    deterministic_decisions JSONB NOT NULL DEFAULT '{}'::jsonb,
    consolidator_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    provider_id TEXT,
    model_id TEXT,
    decision_digest TEXT NOT NULL,
    invalidated_at TIMESTAMPTZ,
    redacted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_id, owner_type, owner_id, decision_digest),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_decision_sets_space_range
    ON omnix_memory_v2_consolidation_decision_sets(
        principal_id, owner_type, owner_id,
        input_observation_from, input_observation_through
    );

CREATE TABLE IF NOT EXISTS omnix_memory_v2_derived_state (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    derived_revision BIGINT NOT NULL DEFAULT 0 CHECK (derived_revision >= 0),
    source_observation_watermark BIGINT NOT NULL DEFAULT 0 CHECK (source_observation_watermark >= 0),
    source_governance_revision BIGINT NOT NULL DEFAULT 0 CHECK (source_governance_revision >= 0),
    decision_set_id TEXT,
    policy_digest TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (decision_set_id)
        REFERENCES omnix_memory_v2_consolidation_decision_sets(decision_set_id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_derived_revisions (
    revision_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    derived_revision BIGINT NOT NULL CHECK (derived_revision >= 1),
    source_observation_watermark BIGINT NOT NULL CHECK (source_observation_watermark >= 0),
    source_governance_revision BIGINT NOT NULL CHECK (source_governance_revision >= 0),
    decision_set_id TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_id, owner_type, owner_id, derived_revision),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (decision_set_id)
        REFERENCES omnix_memory_v2_consolidation_decision_sets(decision_set_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_derived_policy_envelopes (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('assertion', 'episode', 'relationship', 'affect')),
    ref_id TEXT NOT NULL,
    sensitivity TEXT NOT NULL CHECK (sensitivity IN ('normal', 'sensitive', 'secret')),
    effective_visibility JSONB NOT NULL,
    trust_class TEXT NOT NULL CHECK (trust_class IN (
        'user_explicit', 'system_trusted', 'assistant_inference',
        'external_untrusted', 'imported_unverified'
    )),
    source_observation_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_assertion_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_governance_revision BIGINT NOT NULL CHECK (source_governance_revision >= 0),
    policy_version TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    derived_revision BIGINT NOT NULL CHECK (derived_revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id, item_type, ref_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_policy_ref
    ON omnix_memory_v2_derived_policy_envelopes(item_type, ref_id);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_derive_jobs (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    target_observation_watermark BIGINT NOT NULL CHECK (target_observation_watermark >= 0),
    target_governance_revision BIGINT NOT NULL CHECK (target_governance_revision >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_projection_jobs (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    target_derived_revision BIGINT NOT NULL CHECK (target_derived_revision >= 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);
