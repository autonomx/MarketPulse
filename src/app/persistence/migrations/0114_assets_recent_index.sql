-- omnix-migration: phase=expand transactional=false
-- WP-7.6: the unfiltered asset list (newest first) read every asset of the
-- workspace and sorted them, 32 ms per page at 100k assets. The
-- (workspace_id, asset_type, ...) index serves only type-filtered lists.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_omnix_assets_workspace_recent
    ON omnix_assets (workspace_id, created_at DESC, id DESC)
    WHERE lifecycle_status <> 'deleted'
