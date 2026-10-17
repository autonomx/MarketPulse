CREATE TABLE IF NOT EXISTS omnix_memory_v2_graph_state (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    graph_revision BIGINT NOT NULL DEFAULT 0 CHECK (graph_revision >= 0),
    source_observation_watermark BIGINT NOT NULL DEFAULT 0 CHECK (source_observation_watermark >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_graph_entities (
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (principal_id, owner_type, owner_id, entity_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_graph_assertions (
    assertion_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    visibility_scopes JSONB NOT NULL,
    subject_entity_id TEXT NOT NULL,
    subject_entity_type TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_value JSONB NOT NULL,
    domain TEXT NOT NULL,
    assertion_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    derivation_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'retracted', 'disputed')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (assertion_id, principal_id, owner_type, owner_id),
    CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_until >= valid_from),
    FOREIGN KEY (principal_id, owner_type, owner_id, subject_entity_id)
        REFERENCES omnix_memory_v2_graph_entities(principal_id, owner_type, owner_id, entity_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_state(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_assertions_space_status
    ON omnix_memory_v2_graph_assertions(principal_id, owner_type, owner_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_v2_assertions_subject_predicate
    ON omnix_memory_v2_graph_assertions(principal_id, owner_type, owner_id, subject_entity_id, predicate);
CREATE INDEX IF NOT EXISTS idx_memory_v2_assertions_domain
    ON omnix_memory_v2_graph_assertions(principal_id, owner_type, owner_id, domain);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_assertion_observation_evidence (
    assertion_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (assertion_id, observation_id),
    FOREIGN KEY (assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_assertion_assertion_evidence (
    assertion_id TEXT NOT NULL,
    evidence_assertion_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (assertion_id, evidence_assertion_id),
    CHECK (assertion_id <> evidence_assertion_id),
    FOREIGN KEY (assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (evidence_assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_assertion_relations (
    assertion_id TEXT NOT NULL,
    related_assertion_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('supersedes', 'contradicted_by')),
    PRIMARY KEY (assertion_id, related_assertion_id, relation_type),
    CHECK (assertion_id <> related_assertion_id),
    FOREIGN KEY (assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (related_assertion_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_graph_assertions(assertion_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);
