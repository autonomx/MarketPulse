CREATE TABLE IF NOT EXISTS omnix_companion_initiative_sessions (
    session_id TEXT PRIMARY KEY,
    generation TEXT,
    active_lease_id TEXT,
    active_owner TEXT,
    active_intent_id TEXT,
    active_channel TEXT CHECK (
        active_channel IS NULL OR active_channel IN ('text', 'avatar', 'voice', 'notification')
    ),
    active_urgency TEXT CHECK (
        active_urgency IS NULL OR active_urgency IN ('low', 'normal', 'high', 'critical')
    ),
    active_interruptibility TEXT CHECK (
        active_interruptibility IS NULL OR active_interruptibility IN (
            'never', 'idle_only', 'floor_available', 'interrupt'
        )
    ),
    active_acquired_at TIMESTAMPTZ,
    active_expires_at TIMESTAMPTZ,
    last_delivered_at TIMESTAMPTZ,
    last_delivered_owner TEXT,
    consecutive_deliveries_by_owner INTEGER NOT NULL DEFAULT 0
        CHECK (consecutive_deliveries_by_owner >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        active_lease_id IS NULL OR (
            generation IS NOT NULL
            AND active_owner IS NOT NULL
            AND active_intent_id IS NOT NULL
            AND active_channel IS NOT NULL
            AND active_urgency IS NOT NULL
            AND active_interruptibility IS NOT NULL
            AND active_acquired_at IS NOT NULL
            AND active_expires_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS omnix_companion_initiative_active_expiry_idx
    ON omnix_companion_initiative_sessions(active_expires_at)
    WHERE active_lease_id IS NOT NULL;
