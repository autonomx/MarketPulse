-- omnix-migration: phase=expand transactional=true
-- MarketPulse S-6: alert and margin-call deliveries can go to the desktop app's notifications.
--
-- The desktop shell runs a loopback listener; the delivery monitor sends 'desktop' rows to it like any other
-- channel. Rows are only written when the shell configured the listener, so Omnix in a browser writes none.

ALTER TABLE omnix_trading_notification_deliveries
    DROP CONSTRAINT IF EXISTS omnix_trading_notification_deliveries_channel_check;
ALTER TABLE omnix_trading_notification_deliveries
    ADD CONSTRAINT omnix_trading_notification_deliveries_channel_check CHECK (
        channel IN ('webhook', 'email', 'push', 'desktop')
    );
