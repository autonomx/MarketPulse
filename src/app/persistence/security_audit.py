from __future__ import annotations

from typing import Any


_REQUIRED_COMPOSITE_CONSTRAINTS = frozenset(
    {
        "fk_omnix_chat_messages_workspace_session",
        "fk_omnix_job_events_workspace_job",
        "fk_omnix_rpg_turns_workspace_campaign",
        "fk_omnix_rpg_interactions_workspace_campaign",
        "fk_omnix_rpg_interactions_workspace_turn",
        "fk_omnix_rpg_snapshots_workspace_campaign",
        "fk_omnix_rpg_participants_workspace_campaign",
    }
)


def tenant_security_audit(connection: Any) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT constraint_name
          FROM information_schema.table_constraints
         WHERE constraint_schema = current_schema()
           AND constraint_type = 'FOREIGN KEY'
           AND constraint_name = ANY(%s)
         LIMIT %s
        """,
        (sorted(_REQUIRED_COMPOSITE_CONSTRAINTS), len(_REQUIRED_COMPOSITE_CONSTRAINTS)),
    ).fetchall()
    constraints = {str(row[0]) for row in rows}
    missing = sorted(_REQUIRED_COMPOSITE_CONSTRAINTS - constraints)
    policy = connection.execute(
        """
        SELECT runtime_role_policy, migration_role_separate,
               backup_role_separate, rls_decision,
               remote_access_requires_rls_review
          FROM omnix_security_policy_state
         WHERE singleton = TRUE
        """
    ).fetchone()
    if policy is None:
        return {"ok": False, "missing_constraints": missing, "policy": None}
    result = {
        "runtime_role_policy": str(policy[0]),
        "migration_role_separate": bool(policy[1]),
        "backup_role_separate": bool(policy[2]),
        "rls_decision": str(policy[3]),
        "remote_access_requires_rls_review": bool(policy[4]),
    }
    ok = (
        not missing
        and result["runtime_role_policy"] == "least_privilege"
        and result["migration_role_separate"]
        and result["backup_role_separate"]
        and result["remote_access_requires_rls_review"]
    )
    return {"ok": ok, "missing_constraints": missing, "policy": result}
