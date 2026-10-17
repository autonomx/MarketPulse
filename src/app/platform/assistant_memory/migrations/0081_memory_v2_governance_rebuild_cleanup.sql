CREATE OR REPLACE FUNCTION omnix_memory_v2_cleanup_governance_rebuild()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.source_governance_revision = OLD.source_governance_revision THEN
        RETURN NEW;
    END IF;

    -- A governance-triggered derivation is a full replacement, not an incremental merge.
    -- The coordinator writes the complete replacement set first and stamps every retained
    -- item policy with NEW.derived_revision. Anything not refreshed into that revision is
    -- stale derived state and must disappear atomically with the revision commit.
    DELETE FROM omnix_memory_v2_affect_observations a
     WHERE a.principal_id = NEW.principal_id
       AND a.owner_type = NEW.owner_type
       AND a.owner_id = NEW.owner_id
       AND NOT EXISTS (
            SELECT 1
              FROM omnix_memory_v2_derived_policy_envelopes p
             WHERE p.principal_id = NEW.principal_id
               AND p.owner_type = NEW.owner_type
               AND p.owner_id = NEW.owner_id
               AND p.item_type = 'affect'
               AND p.ref_id = a.affect_id
               AND p.derived_revision = NEW.derived_revision
       );

    DELETE FROM omnix_memory_v2_relationships r
     WHERE r.principal_id = NEW.principal_id
       AND r.owner_type = NEW.owner_type
       AND r.owner_id = NEW.owner_id
       AND NOT EXISTS (
            SELECT 1
              FROM omnix_memory_v2_derived_policy_envelopes p
             WHERE p.principal_id = NEW.principal_id
               AND p.owner_type = NEW.owner_type
               AND p.owner_id = NEW.owner_id
               AND p.item_type = 'relationship'
               AND p.ref_id = r.relationship_id
               AND p.derived_revision = NEW.derived_revision
       );

    DELETE FROM omnix_memory_v2_episodes e
     WHERE e.principal_id = NEW.principal_id
       AND e.owner_type = NEW.owner_type
       AND e.owner_id = NEW.owner_id
       AND NOT EXISTS (
            SELECT 1
              FROM omnix_memory_v2_derived_policy_envelopes p
             WHERE p.principal_id = NEW.principal_id
               AND p.owner_type = NEW.owner_type
               AND p.owner_id = NEW.owner_id
               AND p.item_type = 'episode'
               AND p.ref_id = e.episode_id
               AND p.derived_revision = NEW.derived_revision
       );

    -- Deleting stale assertions after stale episodes preserves the graph's RESTRICT
    -- evidence constraints. If a retained assertion/episode still references an omitted
    -- assertion, PostgreSQL rejects the transaction instead of silently weakening proof.
    DELETE FROM omnix_memory_v2_graph_assertions a
     WHERE a.principal_id = NEW.principal_id
       AND a.owner_type = NEW.owner_type
       AND a.owner_id = NEW.owner_id
       AND NOT EXISTS (
            SELECT 1
              FROM omnix_memory_v2_derived_policy_envelopes p
             WHERE p.principal_id = NEW.principal_id
               AND p.owner_type = NEW.owner_type
               AND p.owner_id = NEW.owner_id
               AND p.item_type = 'assertion'
               AND p.ref_id = a.assertion_id
               AND p.derived_revision = NEW.derived_revision
       );

    DELETE FROM omnix_memory_v2_derived_policy_envelopes p
     WHERE p.principal_id = NEW.principal_id
       AND p.owner_type = NEW.owner_type
       AND p.owner_id = NEW.owner_id
       AND p.derived_revision <> NEW.derived_revision;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_memory_v2_governance_rebuild_cleanup
    ON omnix_memory_v2_derived_state;

CREATE TRIGGER trg_memory_v2_governance_rebuild_cleanup
AFTER UPDATE OF source_governance_revision, derived_revision
ON omnix_memory_v2_derived_state
FOR EACH ROW
WHEN (NEW.source_governance_revision IS DISTINCT FROM OLD.source_governance_revision)
EXECUTE FUNCTION omnix_memory_v2_cleanup_governance_rebuild();
