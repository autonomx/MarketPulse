CREATE OR REPLACE FUNCTION omnix_restore_persistence_cutover_singleton()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO omnix_persistence_cutover (singleton, mode)
    VALUES (TRUE, 'legacy_preflight')
    ON CONFLICT (singleton) DO NOTHING;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_omnix_restore_persistence_cutover_singleton
    ON omnix_persistence_cutover;

CREATE TRIGGER trg_omnix_restore_persistence_cutover_singleton
AFTER TRUNCATE ON omnix_persistence_cutover
FOR EACH STATEMENT
EXECUTE FUNCTION omnix_restore_persistence_cutover_singleton();
