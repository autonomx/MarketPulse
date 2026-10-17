-- omnix-migration: phase=expand transactional=true
-- WP-8.3: fencing tokens for background ownership. Background workers and
-- scheduled tasks are owned through PostgreSQL advisory locks; whoever takes a
-- lock bumps its epoch here. A background transaction reads its owner's epoch
-- with FOR SHARE when it checks out a connection, so it fails once another
-- process owns the lock, and a new owner's bump waits for the old owner's
-- in-flight transactions to finish. Lock keys already encode the workspace,
-- so the table is not per workspace.
CREATE TABLE IF NOT EXISTS omnix_ownership_epochs (
    lock_key BIGINT PRIMARY KEY,
    epoch BIGINT NOT NULL,
    holder TEXT NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
