from __future__ import annotations

from typing import Any, Mapping

from app.persistence.errors import EntityNotFound, RevisionConflict
from .rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


class WorldForgeProposalConflict(RevisionConflict):
    pass


def _row(value: Any) -> dict[str, Any]:
    return {
        "workspace_id": str(value[0]),
        "proposal_id": str(value[1]),
        "campaign_id": str(value[2]),
        "base_bible_revision": int(value[3]),
        "status": str(value[4]),
        "proposal": dict(value[5]),
        "consistency_report": dict(value[6]),
        "proposed_by": str(value[7]),
        "decision_note": str(value[8]),
        "created_at": value[9].isoformat(),
        "decided_at": value[10].isoformat() if value[10] is not None else None,
    }


_COLUMNS = """
workspace_id, proposal_id, campaign_id, base_bible_revision, status,
proposal_jsonb, consistency_report_jsonb, proposed_by, decision_note,
created_at, decided_at
"""


class PostgresRpgWorldForgeRepository:
    """Persistence boundary for reviewable World Forge proposals."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def create(
        self,
        context: TenantContext,
        *,
        proposal_id: str,
        campaign_id: str,
        base_bible_revision: int,
        proposal: Mapping[str, Any],
        consistency_report: Mapping[str, Any],
        proposed_by: str = "world_forge",
    ) -> dict[str, Any]:
        campaign = self.connection.execute(
            "SELECT id FROM omnix_rpg_campaigns WHERE workspace_id = %s AND id = %s",
            (context.workspace_id, campaign_id),
        ).fetchone()
        if campaign is None:
            raise EntityNotFound(campaign_id)
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_world_forge_proposals (
                workspace_id, proposal_id, campaign_id, base_bible_revision,
                proposal_jsonb, consistency_report_jsonb, proposed_by
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                proposal_id,
                campaign_id,
                int(base_bible_revision),
                canonical_json(dict(proposal)),
                canonical_json(dict(consistency_report)),
                proposed_by,
            ),
        ).fetchone()
        return _row(row)

    def get(
        self,
        context: TenantContext,
        proposal_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_rpg_world_forge_proposals "
            f"WHERE workspace_id = %s AND proposal_id = %s{suffix}",
            (context.workspace_id, proposal_id),
        ).fetchone()
        return _row(row) if row is not None else None

    def decide(
        self,
        context: TenantContext,
        *,
        proposal_id: str,
        decision: str,
        consistency_report: Mapping[str, Any],
        decision_note: str = "",
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("World Forge decision must be approved or rejected")
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_world_forge_proposals
               SET status = %s,
                   consistency_report_jsonb = %s::jsonb,
                   decision_note = %s,
                   decided_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND proposal_id = %s AND status = 'proposed'
            RETURNING {_COLUMNS}
            """,
            (
                decision,
                canonical_json(dict(consistency_report)),
                decision_note,
                context.workspace_id,
                proposal_id,
            ),
        ).fetchone()
        if row is None:
            current = self.get(context, proposal_id)
            if current is None:
                raise EntityNotFound(proposal_id)
            raise WorldForgeProposalConflict(
                f"World Forge proposal is already decided: {proposal_id}"
            )
        return _row(row)
