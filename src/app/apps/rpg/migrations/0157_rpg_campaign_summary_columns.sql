-- omnix-migration: phase=expand transactional=true
-- WP-5.6: campaign summary columns for list views. The session list read a
-- projection out of every row's state_jsonb, decompressing each saved game;
-- a trigger now keeps the projection (summary_jsonb) and the turn count,
-- player level and location name next to the state on every state write,
-- so a list row reads only these columns. The projection is the list
-- summary source (app.apps.rpg.session.list_summaries); the typed columns
-- follow the turn response's session summary
-- (app.apps.rpg.narration.presentation.turn_response._session_summary).
ALTER TABLE omnix_rpg_campaigns
    ADD COLUMN IF NOT EXISTS summary_jsonb JSONB,
    ADD COLUMN IF NOT EXISTS turn_count BIGINT,
    ADD COLUMN IF NOT EXISTS player_level INTEGER,
    ADD COLUMN IF NOT EXISTS location_name TEXT;

CREATE OR REPLACE FUNCTION omnix_rpg_campaign_list_summary(doc JSONB)
RETURNS JSONB
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT jsonb_build_object(
        'manifest', doc -> 'manifest',
        'state', jsonb_build_object(
            'world', jsonb_build_object(
                'environment', doc #> '{state,world,environment}',
                'regions', (
                    SELECT jsonb_object_agg(region.key, jsonb_build_object(
                               'environment', region.value -> 'environment'))
                      FROM jsonb_each(CASE
                               WHEN jsonb_typeof(doc #> '{state,world,regions}') = 'object'
                               THEN doc #> '{state,world,regions}'
                               ELSE '{}'::jsonb END) AS region
                     WHERE jsonb_typeof(region.value) = 'object')),
            'scene', jsonb_build_object(
                'environment_context', doc #> '{state,scene,environment_context}'),
            'location', doc #> '{state,location}',
            'current_location', doc #> '{state,current_location}',
            'current_location_id', doc #> '{state,current_location_id}',
            'current_location_name', doc #> '{state,current_location_name}',
            'published_world', doc #> '{state,published_world}',
            'environment_snapshot', doc #> '{state,environment_snapshot}'))
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_json_integer(value JSONB)
RETURNS BIGINT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN trunc((value #>> '{}')::numeric)::bigint END
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_json_text(value JSONB)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'string' THEN NULLIF(btrim(value #>> '{}'), '') END
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_campaign_turn_count(doc JSONB)
RETURNS BIGINT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT COALESCE(
        NULLIF(omnix_rpg_json_integer(doc #> '{manifest,turn_count}'), 0),
        omnix_rpg_json_integer(doc -> 'turn_count'))
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_campaign_player_level(doc JSONB)
RETURNS INTEGER
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT COALESCE(
        omnix_rpg_json_integer(doc #> '{state,player,level}'),
        omnix_rpg_json_integer(doc #> '{simulation_state,player_state,level}'))::integer
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_campaign_location_name(doc JSONB)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT COALESCE(
        omnix_rpg_json_text(doc #> '{state,scene,location_name}'),
        omnix_rpg_json_text(doc #> '{state,scene,location}'),
        omnix_rpg_json_text(doc #> '{state,location}'),
        omnix_rpg_json_text(doc #> '{simulation_state,location}'))
$$;

CREATE OR REPLACE FUNCTION omnix_rpg_campaign_summary_columns()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    doc JSONB := NEW.state_jsonb;
BEGIN
    NEW.summary_jsonb := omnix_rpg_campaign_list_summary(doc);
    NEW.turn_count := omnix_rpg_campaign_turn_count(doc);
    NEW.player_level := omnix_rpg_campaign_player_level(doc);
    NEW.location_name := omnix_rpg_campaign_location_name(doc);
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS omnix_rpg_campaign_summary_columns ON omnix_rpg_campaigns;
CREATE TRIGGER omnix_rpg_campaign_summary_columns
    BEFORE INSERT OR UPDATE OF state_jsonb ON omnix_rpg_campaigns
    FOR EACH ROW EXECUTE FUNCTION omnix_rpg_campaign_summary_columns();

-- Row-level security applies to the owner too: backfill every workspace's rows.
SELECT set_config('omnix.system', 'on', true);
UPDATE omnix_rpg_campaigns
   SET summary_jsonb = omnix_rpg_campaign_list_summary(state_jsonb),
       turn_count = omnix_rpg_campaign_turn_count(state_jsonb),
       player_level = omnix_rpg_campaign_player_level(state_jsonb),
       location_name = omnix_rpg_campaign_location_name(state_jsonb)
 WHERE summary_jsonb IS NULL;
SELECT set_config('omnix.system', '', true);

ALTER TABLE omnix_rpg_campaigns ALTER COLUMN summary_jsonb SET NOT NULL;
