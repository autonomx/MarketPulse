-- omnix-migration: phase=expand transactional=true
-- TVP-7.2b: paper margin calls notify by email and push through the notification outbox.
--
-- * The outbox (0142) carried alert triggers only. A delivery now has an event_kind: 'alert' (a trigger of an alert,
--   as before) or 'margin_call' (a paper account's margin-call order, with neither trigger nor alert). Margin calls go
--   to the workspace's email and push channels only: a webhook belongs to one alert.
-- * notify_margin_calls on a paper account: off by default, so existing and strategy-owned accounts send nothing new.

ALTER TABLE omnix_trading_notification_deliveries
    ALTER COLUMN trigger_id DROP NOT NULL;
ALTER TABLE omnix_trading_notification_deliveries
    ALTER COLUMN alert_id DROP NOT NULL;
ALTER TABLE omnix_trading_notification_deliveries
    ADD COLUMN IF NOT EXISTS event_kind TEXT NOT NULL DEFAULT 'alert';

ALTER TABLE omnix_trading_notification_deliveries
    DROP CONSTRAINT IF EXISTS omnix_trading_notification_deliveries_event_kind_check;
ALTER TABLE omnix_trading_notification_deliveries
    ADD CONSTRAINT omnix_trading_notification_deliveries_event_kind_check CHECK (
        event_kind IN ('alert', 'margin_call')
    );
ALTER TABLE omnix_trading_notification_deliveries
    DROP CONSTRAINT IF EXISTS omnix_trading_notification_deliveries_event_subject_check;
ALTER TABLE omnix_trading_notification_deliveries
    ADD CONSTRAINT omnix_trading_notification_deliveries_event_subject_check CHECK (
        (event_kind = 'alert') = (trigger_id IS NOT NULL AND alert_id IS NOT NULL)
    );
ALTER TABLE omnix_trading_notification_deliveries
    DROP CONSTRAINT IF EXISTS omnix_trading_notification_deliveries_event_channel_check;
ALTER TABLE omnix_trading_notification_deliveries
    ADD CONSTRAINT omnix_trading_notification_deliveries_event_channel_check CHECK (
        event_kind = 'alert' OR channel <> 'webhook'
    );

ALTER TABLE omnix_trading_paper_accounts
    ADD COLUMN IF NOT EXISTS notify_margin_calls BOOLEAN NOT NULL DEFAULT FALSE;
