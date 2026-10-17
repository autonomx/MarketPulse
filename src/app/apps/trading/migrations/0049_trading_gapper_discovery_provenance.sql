-- Persist immutable discovery-source provenance for point-in-time gapper universes.
-- Finviz supplies the ranked morning cohort only; candidate market evidence remains
-- independently enriched and execution authority remains unchanged.

ALTER TABLE omnix_trading_gapper_universes
    ADD COLUMN IF NOT EXISTS source_locator TEXT,
    ADD COLUMN IF NOT EXISTS source_candidate_symbols JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE omnix_trading_gapper_universes
    ADD CONSTRAINT omnix_trading_gapper_universes_source_symbols_array
    CHECK (jsonb_typeof(source_candidate_symbols) = 'array');
