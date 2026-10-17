ALTER TABLE omnix_chat_sessions
    ADD CONSTRAINT uq_omnix_chat_sessions_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE omnix_chat_messages
    DROP CONSTRAINT IF EXISTS omnix_chat_messages_session_id_fkey,
    ADD CONSTRAINT fk_omnix_chat_messages_workspace_session
        FOREIGN KEY (workspace_id, session_id)
        REFERENCES omnix_chat_sessions(workspace_id, id)
        ON DELETE CASCADE;

ALTER TABLE omnix_jobs
    ADD CONSTRAINT uq_omnix_jobs_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE omnix_job_events
    DROP CONSTRAINT IF EXISTS omnix_job_events_job_id_fkey,
    ADD CONSTRAINT fk_omnix_job_events_workspace_job
        FOREIGN KEY (workspace_id, job_id)
        REFERENCES omnix_jobs(workspace_id, id)
        ON DELETE CASCADE;

ALTER TABLE omnix_rpg_campaigns
    ADD CONSTRAINT uq_omnix_rpg_campaigns_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE omnix_rpg_turns
    ADD CONSTRAINT uq_omnix_rpg_turns_workspace_id UNIQUE (workspace_id, id),
    DROP CONSTRAINT IF EXISTS omnix_rpg_turns_campaign_id_fkey,
    ADD CONSTRAINT fk_omnix_rpg_turns_workspace_campaign
        FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns(workspace_id, id)
        ON DELETE CASCADE;

ALTER TABLE omnix_rpg_interactions
    DROP CONSTRAINT IF EXISTS omnix_rpg_interactions_campaign_id_fkey,
    DROP CONSTRAINT IF EXISTS omnix_rpg_interactions_turn_id_fkey,
    ADD CONSTRAINT fk_omnix_rpg_interactions_workspace_campaign
        FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns(workspace_id, id)
        ON DELETE CASCADE,
    ADD CONSTRAINT fk_omnix_rpg_interactions_workspace_turn
        FOREIGN KEY (workspace_id, turn_id)
        REFERENCES omnix_rpg_turns(workspace_id, id)
        ON DELETE CASCADE;

ALTER TABLE omnix_rpg_snapshots
    DROP CONSTRAINT IF EXISTS omnix_rpg_snapshots_campaign_id_fkey,
    ADD CONSTRAINT fk_omnix_rpg_snapshots_workspace_campaign
        FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns(workspace_id, id)
        ON DELETE CASCADE;

ALTER TABLE omnix_rpg_participants
    ADD COLUMN IF NOT EXISTS workspace_id TEXT;

UPDATE omnix_rpg_participants AS participant
   SET workspace_id = campaign.workspace_id
  FROM omnix_rpg_campaigns AS campaign
 WHERE participant.campaign_id = campaign.id
   AND participant.workspace_id IS NULL;

CREATE OR REPLACE FUNCTION omnix_set_participant_workspace()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    SELECT workspace_id
      INTO NEW.workspace_id
      FROM omnix_rpg_campaigns
     WHERE id = NEW.campaign_id;
    IF NEW.workspace_id IS NULL THEN
        RAISE EXCEPTION 'campaign % does not exist', NEW.campaign_id
            USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_set_participant_workspace
    ON omnix_rpg_participants;
CREATE TRIGGER trg_omnix_set_participant_workspace
BEFORE INSERT OR UPDATE OF campaign_id
ON omnix_rpg_participants
FOR EACH ROW
EXECUTE FUNCTION omnix_set_participant_workspace();

ALTER TABLE omnix_rpg_participants
    ALTER COLUMN workspace_id SET NOT NULL,
    DROP CONSTRAINT IF EXISTS omnix_rpg_participants_campaign_id_fkey,
    ADD CONSTRAINT fk_omnix_rpg_participants_workspace_campaign
        FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES omnix_rpg_campaigns(workspace_id, id)
        ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_omnix_rpg_participants_workspace_campaign
    ON omnix_rpg_participants (workspace_id, campaign_id, user_id);

CREATE TABLE IF NOT EXISTS omnix_security_policy_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    runtime_role_policy TEXT NOT NULL DEFAULT 'least_privilege',
    migration_role_separate BOOLEAN NOT NULL DEFAULT TRUE,
    backup_role_separate BOOLEAN NOT NULL DEFAULT TRUE,
    rls_decision TEXT NOT NULL DEFAULT 'deferred_local_only'
        CHECK (rls_decision IN ('enabled', 'deferred_local_only')),
    remote_access_requires_rls_review BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO omnix_security_policy_state (singleton)
VALUES (TRUE)
ON CONFLICT (singleton) DO NOTHING;
