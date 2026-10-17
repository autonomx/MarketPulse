CREATE TABLE IF NOT EXISTS omnix_rpg_campaign_spatial_clocks (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    world_tick BIGINT NOT NULL DEFAULT 0 CHECK (world_tick >= 0),
    policy_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    aggregate_metrics_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_npc_spatial_goals (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    goal_revision BIGINT NOT NULL DEFAULT 1 CHECK (goal_revision >= 1),
    actor_id TEXT NOT NULL,
    map_instance_id TEXT NOT NULL,
    goal_type TEXT NOT NULL
        CHECK (goal_type IN ('move_to_cell', 'transition_via_portal')),
    target_cell_jsonb JSONB,
    portal_id TEXT,
    target_map_instance_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    issued_tick BIGINT NOT NULL DEFAULT 0 CHECK (issued_tick >= 0),
    not_before_tick BIGINT NOT NULL DEFAULT 0 CHECK (not_before_tick >= 0),
    expires_after_tick BIGINT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed', 'blocked', 'canceled', 'expired')),
    routine_id TEXT,
    blocked_attempts INTEGER NOT NULL DEFAULT 0 CHECK (blocked_attempts >= 0),
    last_decision_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, campaign_id, goal_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, map_instance_id)
        REFERENCES omnix_rpg_campaign_map_instances (workspace_id, map_instance_id)
        ON DELETE CASCADE,
    CHECK (expires_after_tick IS NULL OR expires_after_tick >= not_before_tick),
    CHECK (
        (goal_type = 'move_to_cell' AND target_cell_jsonb IS NOT NULL
            AND portal_id IS NULL AND target_map_instance_id IS NULL)
        OR
        (goal_type = 'transition_via_portal' AND target_cell_jsonb IS NULL
            AND portal_id IS NOT NULL AND target_map_instance_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS omnix_rpg_npc_spatial_routines (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    routine_id TEXT NOT NULL,
    routine_revision BIGINT NOT NULL DEFAULT 1 CHECK (routine_revision >= 1),
    actor_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    interval_ticks BIGINT NOT NULL CHECK (interval_ticks >= 1),
    document_jsonb JSONB NOT NULL,
    next_step_index INTEGER NOT NULL DEFAULT 0 CHECK (next_step_index >= 0),
    emission_count BIGINT NOT NULL DEFAULT 0 CHECK (emission_count >= 0),
    next_due_tick BIGINT NOT NULL DEFAULT 0 CHECK (next_due_tick >= 0),
    last_issued_tick BIGINT,
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id, routine_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_npc_spatial_tick_runs (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    world_tick BIGINT NOT NULL CHECK (world_tick >= 1),
    result_jsonb JSONB NOT NULL,
    metrics_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id, world_tick),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_rpg_npc_spatial_transitions (
    workspace_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    transition_id TEXT NOT NULL,
    world_tick BIGINT NOT NULL CHECK (world_tick >= 1),
    actor_id TEXT NOT NULL,
    portal_id TEXT NOT NULL,
    source_map_instance_id TEXT NOT NULL,
    target_map_instance_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    target_event_id TEXT NOT NULL,
    payload_jsonb JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, campaign_id, transition_id),
    UNIQUE (workspace_id, source_event_id),
    UNIQUE (workspace_id, target_event_id),
    FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, source_map_instance_id)
        REFERENCES omnix_rpg_campaign_map_instances (workspace_id, map_instance_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, target_map_instance_id)
        REFERENCES omnix_rpg_campaign_map_instances (workspace_id, map_instance_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS omnix_rpg_npc_spatial_goals_active_idx
    ON omnix_rpg_npc_spatial_goals (
        workspace_id, campaign_id, status, map_instance_id, actor_id, priority DESC
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_npc_spatial_routines_due_idx
    ON omnix_rpg_npc_spatial_routines (
        workspace_id, campaign_id, enabled, next_due_tick, routine_id
    );
CREATE INDEX IF NOT EXISTS omnix_rpg_npc_spatial_tick_runs_recent_idx
    ON omnix_rpg_npc_spatial_tick_runs (
        workspace_id, campaign_id, world_tick DESC
    );

COMMENT ON TABLE omnix_rpg_campaign_spatial_clocks IS
    'Serialized deterministic campaign clock and measured spatial policy metrics.';
COMMENT ON TABLE omnix_rpg_npc_spatial_goals IS
    'Campaign-owned current NPC spatial goals with optimistic revisions and outcomes.';
COMMENT ON TABLE omnix_rpg_npc_spatial_routines IS
    'Authored deterministic NPC spatial routines that emit durable goals on campaign ticks.';
COMMENT ON TABLE omnix_rpg_npc_spatial_tick_runs IS
    'Auditable per-tick decisions and budget metrics for living NPC spatial simulation.';
COMMENT ON TABLE omnix_rpg_npc_spatial_transitions IS
    'Atomic cross-map portal transfers correlated across source and target map event streams.';
