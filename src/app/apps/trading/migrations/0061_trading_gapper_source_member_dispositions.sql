-- Preserve exact ranked discovery-member accounting across PostgreSQL reloads.
-- Historical universes intentionally default to an empty disposition set: missing
-- provenance must remain fail-closed rather than being reconstructed heuristically.

ALTER TABLE omnix_trading_gapper_universes
    ADD COLUMN IF NOT EXISTS source_member_dispositions JSONB NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'omnix_trading_gapper_universes_source_dispositions_array'
    ) THEN
        ALTER TABLE omnix_trading_gapper_universes
            ADD CONSTRAINT omnix_trading_gapper_universes_source_dispositions_array
            CHECK (jsonb_typeof(source_member_dispositions) = 'array');
    END IF;
END
$$;
