CREATE TABLE IF NOT EXISTS omnix_characters (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    visibility TEXT NOT NULL DEFAULT 'private',
    active_version BIGINT NOT NULL DEFAULT 1 CHECK (active_version >= 1),
    status TEXT NOT NULL DEFAULT 'active',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    profile JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_characters_workspace_status
    ON omnix_characters (workspace_id, status, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS omnix_character_versions (
    character_id TEXT NOT NULL REFERENCES omnix_characters(id) ON DELETE CASCADE,
    version BIGINT NOT NULL CHECK (version >= 1),
    profile JSONB NOT NULL,
    created_by TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (character_id, version)
);

CREATE TABLE IF NOT EXISTS omnix_conversation_segments (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    interaction_mode TEXT NOT NULL,
    character_id TEXT REFERENCES omnix_characters(id) ON DELETE SET NULL,
    character_version BIGINT,
    transcript_policy TEXT NOT NULL,
    read_memory BOOLEAN NOT NULL DEFAULT FALSE,
    write_memory BOOLEAN NOT NULL DEFAULT FALSE,
    shared_memory_access TEXT NOT NULL DEFAULT 'none',
    carryover_summary TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_omnix_segments_session_started
    ON omnix_conversation_segments (workspace_id, session_id, started_at, id);

CREATE TABLE IF NOT EXISTS omnix_memory_records (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    pinned BOOLEAN NOT NULL DEFAULT FALSE,
    trust_level TEXT NOT NULL DEFAULT 'normal',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    provenance_type TEXT,
    provenance_id TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_omnix_memory_owner_status
    ON omnix_memory_records (workspace_id, owner_type, owner_id, status, pinned DESC, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS omnix_memory_candidates (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    source_session_id TEXT,
    source_message_id TEXT NOT NULL,
    candidate_fingerprint TEXT NOT NULL,
    proposed_owner_type TEXT NOT NULL,
    proposed_owner_id TEXT NOT NULL,
    proposed_category TEXT NOT NULL,
    proposed_content TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source TEXT NOT NULL,
    trust_level TEXT NOT NULL DEFAULT 'normal',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    extraction_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ,
    UNIQUE (workspace_id, source_message_id, candidate_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_omnix_memory_candidates_status
    ON omnix_memory_candidates (workspace_id, status, created_at, id);

CREATE TABLE IF NOT EXISTS omnix_memory_snapshots (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    revision BIGINT NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workspace_id, owner_type, owner_id, revision)
);

CREATE TABLE IF NOT EXISTS omnix_memory_snapshot_items (
    snapshot_id TEXT NOT NULL REFERENCES omnix_memory_snapshots(id) ON DELETE CASCADE,
    memory_record_id TEXT NOT NULL REFERENCES omnix_memory_records(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    record_revision BIGINT NOT NULL CHECK (record_revision >= 1),
    PRIMARY KEY (snapshot_id, memory_record_id),
    UNIQUE (snapshot_id, position)
);

CREATE TABLE IF NOT EXISTS omnix_memory_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_memory_events_entity
    ON omnix_memory_events (workspace_id, entity_type, entity_id, id);

CREATE TABLE IF NOT EXISTS omnix_chat_sessions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES omnix_users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    provider_id TEXT,
    model_id TEXT,
    project_id TEXT,
    profile_id TEXT,
    interaction_mode TEXT NOT NULL DEFAULT 'system',
    character_id TEXT REFERENCES omnix_characters(id) ON DELETE SET NULL,
    character_version BIGINT,
    memory_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    memory_snapshot_id TEXT REFERENCES omnix_memory_snapshots(id) ON DELETE SET NULL,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    transcript_policy TEXT NOT NULL DEFAULT 'persistent',
    active_segment_id TEXT REFERENCES omnix_conversation_segments(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    message_count BIGINT NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_omnix_chat_sessions_workspace_updated
    ON omnix_chat_sessions (workspace_id, status, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS omnix_chat_messages (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES omnix_chat_sessions(id) ON DELETE CASCADE,
    position BIGINT NOT NULL CHECK (position >= 0),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, position)
);

CREATE INDEX IF NOT EXISTS idx_omnix_chat_messages_session_position
    ON omnix_chat_messages (workspace_id, session_id, position, id);
