-- omnix-migration: phase=expand transactional=true
-- TVP-0.5a: the notification outbox.
--
-- One row per alert trigger and delivery channel, written in the transaction
-- that records the trigger, so a trigger is never lost and never delivered
-- twice by the outbox itself. The delivery monitor claims due rows with a
-- lease, sends outside the transaction and records the result. Delivery is
-- at-least-once: a sender that crashes after sending but before recording
-- sends again once its lease expires, with the same delivery id.
--
-- * status: pending (waiting for next_attempt_at), sending (claimed until
--   lease_expires_at), delivered or failed (no more attempts).
-- * message is the text the trigger produced; later edits to the alert's
--   message do not change what this trigger sends. The destination is read
--   from the alert when sending (webhook URLs and secrets are kept out of
--   PostgreSQL), so a corrected URL also serves pending retries.
-- * last_error holds a reason code and never a URL, secret or response body.

CREATE TABLE IF NOT EXISTS omnix_trading_notification_deliveries (
    workspace_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    trigger_id TEXT NOT NULL,
    alert_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('webhook', 'email', 'push')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sending', 'delivered', 'failed')),
    message TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    last_status_code INTEGER,
    delivered_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, delivery_id),
    UNIQUE (workspace_id, idempotency_key),
    FOREIGN KEY (workspace_id, trigger_id)
        REFERENCES omnix_trading_alert_triggers(workspace_id, trigger_id)
        ON DELETE CASCADE,
    CHECK ((status = 'sending') = (lease_expires_at IS NOT NULL)),
    CHECK ((status = 'delivered') = (delivered_at IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_omnix_trading_notification_deliveries_due
    ON omnix_trading_notification_deliveries (workspace_id, next_attempt_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_omnix_trading_notification_deliveries_leased
    ON omnix_trading_notification_deliveries (workspace_id, lease_expires_at)
    WHERE status = 'sending';

CREATE INDEX IF NOT EXISTS idx_omnix_trading_notification_deliveries_alert
    ON omnix_trading_notification_deliveries (workspace_id, alert_id, created_at DESC);

ALTER TABLE omnix_trading_notification_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE omnix_trading_notification_deliveries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON omnix_trading_notification_deliveries;
CREATE POLICY tenant_isolation ON omnix_trading_notification_deliveries
    USING (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on')
    WITH CHECK (workspace_id = current_setting('omnix.workspace_id', true) OR current_setting('omnix.system', true) = 'on');
