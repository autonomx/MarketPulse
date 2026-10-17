CREATE TABLE IF NOT EXISTS omnix_memory_v2_relationships (
    relationship_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('system', 'character')),
    owner_id TEXT NOT NULL,
    subject_entity_id TEXT NOT NULL,
    subject_entity_type TEXT NOT NULL,
    counterpart_entity_id TEXT NOT NULL,
    counterpart_entity_type TEXT NOT NULL,
    prompt_interpretation TEXT NOT NULL,
    derivation_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'archived')),
    revision BIGINT NOT NULL CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (relationship_id, principal_id, owner_type, owner_id),
    FOREIGN KEY (principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_authority_streams(principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_v2_relationships_space_status
    ON omnix_memory_v2_relationships(principal_id, owner_type, owner_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_v2_relationships_counterpart
    ON omnix_memory_v2_relationships(
        principal_id, owner_type, owner_id, counterpart_entity_id
    );

CREATE TABLE IF NOT EXISTS omnix_memory_v2_relationship_metrics (
    relationship_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL CHECK (metric_value >= 0.0 AND metric_value <= 1.0),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    PRIMARY KEY (relationship_id, metric_name),
    UNIQUE (relationship_id, metric_name, principal_id, owner_type, owner_id),
    FOREIGN KEY (relationship_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_relationships(relationship_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_relationship_evidence (
    relationship_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (relationship_id, observation_id),
    FOREIGN KEY (relationship_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_relationships(relationship_id, principal_id, owner_type, owner_id)
        ON DELETE CASCADE,
    FOREIGN KEY (observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS omnix_memory_v2_relationship_metric_evidence (
    relationship_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (relationship_id, metric_name, observation_id),
    FOREIGN KEY (relationship_id, metric_name, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_relationship_metrics(
            relationship_id, metric_name, principal_id, owner_type, owner_id
        ) ON DELETE CASCADE,
    FOREIGN KEY (observation_id, principal_id, owner_type, owner_id)
        REFERENCES omnix_memory_v2_observations(observation_id, principal_id, owner_type, owner_id)
        ON DELETE RESTRICT
);
