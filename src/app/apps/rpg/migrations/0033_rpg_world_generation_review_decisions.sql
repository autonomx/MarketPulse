CREATE OR REPLACE FUNCTION omnix_preserve_world_generation_review_decisions()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.plan_jsonb ? 'review_decisions'
       AND NOT NEW.plan_jsonb ? 'review_decisions' THEN
        NEW.plan_jsonb := NEW.plan_jsonb || jsonb_build_object(
            'review_decisions', OLD.plan_jsonb->'review_decisions'
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS omnix_world_generation_review_decisions_guard
    ON omnix_rpg_world_generation_runs;

CREATE TRIGGER omnix_world_generation_review_decisions_guard
BEFORE UPDATE OF plan_jsonb ON omnix_rpg_world_generation_runs
FOR EACH ROW
EXECUTE FUNCTION omnix_preserve_world_generation_review_decisions();

COMMENT ON FUNCTION omnix_preserve_world_generation_review_decisions() IS
    'Preserves explicit Game Master keep/replace decisions when generation reconciliation refreshes the run plan.';
