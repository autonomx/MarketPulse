-- omnix-migration: phase=expand transactional=true
CREATE TABLE IF NOT EXISTS omnix_device_capacity (
    device_id TEXT NOT NULL,
    model_class TEXT NOT NULL CHECK (model_class IN ('tts', 'stt', 'image', 'llm-local')),
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    realtime_reserved_units INTEGER NOT NULL DEFAULT 0,
    configured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, model_class),
    CHECK (realtime_reserved_units >= 0 AND realtime_reserved_units <= capacity)
);

CREATE TABLE IF NOT EXISTS omnix_device_permit_requests (
    request_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    model_class TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    priority TEXT NOT NULL CHECK (priority IN ('realtime', 'interactive', 'batch')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (device_id, model_class)
        REFERENCES omnix_device_capacity(device_id, model_class) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_device_permit_requests_queue
    ON omnix_device_permit_requests (device_id, model_class, priority, requested_at, request_id);
CREATE INDEX IF NOT EXISTS idx_omnix_device_permit_requests_expiry
    ON omnix_device_permit_requests (expires_at);

CREATE TABLE IF NOT EXISTS omnix_device_permits (
    permit_id TEXT PRIMARY KEY,
    lease_token TEXT NOT NULL,
    device_id TEXT NOT NULL,
    model_class TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK (units > 0),
    priority TEXT NOT NULL CHECK (priority IN ('realtime', 'interactive', 'batch')),
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (device_id, model_class)
        REFERENCES omnix_device_capacity(device_id, model_class) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_omnix_device_permits_active
    ON omnix_device_permits (device_id, model_class, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_omnix_device_permits_holder
    ON omnix_device_permits (holder_id, lease_expires_at);

CREATE TABLE IF NOT EXISTS omnix_device_model_owners (
    device_id TEXT NOT NULL,
    model_class TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (device_id, model_class),
    FOREIGN KEY (device_id, model_class)
        REFERENCES omnix_device_capacity(device_id, model_class) ON DELETE CASCADE
);
