-- Phases 16-18: durable TaskGraph coordinator state.
CREATE TABLE IF NOT EXISTS omnix_task_graph_runs (
    workspace_id TEXT NOT NULL REFERENCES omnix_workspaces(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    graph_id TEXT NOT NULL,
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 1),
    graph JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision >= 1),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_omnix_task_graph_runs_status
    ON omnix_task_graph_runs (workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS omnix_task_graph_revisions (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    graph_revision BIGINT NOT NULL CHECK (graph_revision >= 1),
    user_instruction TEXT,
    graph JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, graph_revision),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_task_graph_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS omnix_task_graph_node_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    child_run_id TEXT,
    output JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_error TEXT,
    fingerprint TEXT NOT NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, run_id, node_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_task_graph_runs(workspace_id, run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_task_graph_nodes_status
    ON omnix_task_graph_node_runs (workspace_id, run_id, status);

CREATE INDEX IF NOT EXISTS idx_omnix_task_graph_nodes_child
    ON omnix_task_graph_node_runs (workspace_id, child_run_id)
    WHERE child_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS omnix_task_graph_events (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id, sequence),
    UNIQUE (workspace_id, event_id),
    FOREIGN KEY (workspace_id, run_id)
        REFERENCES omnix_task_graph_runs(workspace_id, run_id) ON DELETE CASCADE
);
