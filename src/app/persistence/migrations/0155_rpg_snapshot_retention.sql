-- omnix-migration: phase=expand transactional=true
-- WP-5.2: RPG snapshots keep each campaign's newest 20 and every 100th
-- revision; app.apps.rpg.declarations deletes the rest after a one-day grace.
INSERT INTO omnix_retention_policies (record_type, retention_days, terminal_only, enabled, metadata)
VALUES ('rpg_snapshots', 1, FALSE, TRUE,
        '{"keeps": "newest 20 per campaign and every 100th revision"}'::jsonb)
ON CONFLICT (record_type) DO NOTHING;
