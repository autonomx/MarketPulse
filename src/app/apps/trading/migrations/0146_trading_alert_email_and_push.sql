-- omnix-migration: phase=expand transactional=true
-- TVP-0.5b/c: alert email (SMTP) and web-push delivery.
--
-- * omnix_trading_notification_settings: a workspace's SMTP settings; the password lives in the OS-protected secret
--   store (has_password says whether one is set), never here.
-- * omnix_trading_push_subscriptions: a browser's push subscription per user and device. The endpoint is a
--   capability URL of the push service; sending needs it, the browser's keys and Omnix's VAPID key.

CREATE TABLE IF NOT EXISTS omnix_trading_notification_settings (
    workspace_id TEXT PRIMARY KEY,
    email JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS omnix_trading_push_subscriptions (
    workspace_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_success_at TIMESTAMPTZ,
    PRIMARY KEY (workspace_id, subscription_id),
    UNIQUE (workspace_id, endpoint)
);

ALTER TABLE omnix_trading_notification_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_notification_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_notification_settings;
CREATE POLICY tenant_isolation ON omnix_trading_notification_settings
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');

ALTER TABLE omnix_trading_push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_push_subscriptions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_push_subscriptions;
CREATE POLICY tenant_isolation ON omnix_trading_push_subscriptions
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');
