"""Tenant-scoped, single-use capability proposal authority.

Callers supply the operation's transaction. Consumption and execution-ledger
reservation therefore commit together before an external side effect begins.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .tenant import TenantContext

_COLUMNS = "id, capability_id, proposal_digest, proposal_payload, approval_required, decision, expires_at, requested_by"


class CapabilityApprovalConflict(ValueError):
    """A proposal cannot authorize this execution or decision."""


def proposal_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bound_payload(payload: dict[str, Any], capability_id: str) -> dict[str, Any]:
    """The payload with the capability's current definition hash (PA-1.4).

    The proposal digest covers it, so a proposal cannot authorize a capability
    whose authority-relevant definition changed or disappeared since issue.
    The stored payload stays the plain request.
    """
    from app.capabilities.registry import capability_definition_hash

    definition = capability_definition_hash(capability_id)
    if definition is None:
        raise CapabilityApprovalConflict("capability_unavailable")
    return {**payload, "capability_definition_hash": definition}


@dataclass(frozen=True)
class CapabilityProposal:
    id: str
    capability_id: str
    proposal_digest: str
    payload: dict[str, Any]
    approval_required: bool
    decision: str
    expires_at: datetime
    requested_by: str | None = None


class PostgresCapabilityApprovalRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @staticmethod
    def _record(row: Any) -> CapabilityProposal:
        return CapabilityProposal(*row)

    def create(
        self, context: TenantContext, *, capability_id: str,
        payload: dict[str, Any], approval_required: bool, ttl_seconds: int = 900,
        subject_type: str = "tool_proposal", subject_id: str | None = None,
    ) -> CapabilityProposal:
        if not 1 <= ttl_seconds <= 86400:
            raise ValueError("proposal lifetime must be between 1 second and 24 hours")
        identifier = secrets.token_urlsafe(24)
        digest = proposal_digest(_bound_payload(payload, capability_id))
        row = self.connection.execute(
            f"""INSERT INTO omnix_capability_approvals (
                id, workspace_id, subject_type, subject_id, capability_id,
                proposal_digest, proposal_payload, approval_required, requested_by, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                      CURRENT_TIMESTAMP + %s * INTERVAL '1 second')
            RETURNING {_COLUMNS}""",
            (identifier, context.workspace_id, subject_type, subject_id or identifier,
             capability_id, digest, json.dumps(payload, ensure_ascii=False, allow_nan=False),
             approval_required, context.user_id, ttl_seconds),
        ).fetchone()
        return self._record(row)

    def get(self, context: TenantContext, identifier: str, *, lock: bool = False) -> CapabilityProposal | None:
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM omnix_capability_approvals WHERE workspace_id = %s AND id = %s"
            + (" FOR UPDATE" if lock else ""),
            (context.workspace_id, identifier),
        ).fetchone()
        return self._record(row) if row is not None else None

    def decide(
        self, context: TenantContext, identifier: str, *, approve: bool,
        reason: str | None = None,
    ) -> CapabilityProposal:
        if self.get(context, identifier, lock=True) is None:
            raise CapabilityApprovalConflict("proposal_missing_expired_or_already_decided")
        row = self.connection.execute(
            f"""UPDATE omnix_capability_approvals
                   SET decision = %s, decided_by = %s, reason = %s,
                       decided_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND id = %s AND decision = 'pending'
                   AND expires_at > clock_timestamp()
                 RETURNING {_COLUMNS}""",
            ("approved" if approve else "denied", context.user_id, reason,
             context.workspace_id, identifier),
        ).fetchone()
        if row is None:
            raise CapabilityApprovalConflict("proposal_missing_expired_or_already_decided")
        return self._record(row)

    def consume(
        self, context: TenantContext, identifier: str, *, expected_payload: dict[str, Any],
        ledger_payload: dict[str, Any],
    ) -> CapabilityProposal:
        stored = self.get(context, identifier, lock=True)
        if stored is None:
            raise CapabilityApprovalConflict("proposal_not_found")
        # Issued before definitions were bound (PA-1.4): refuse; propose again.
        if hmac.compare_digest(proposal_digest(stored.payload), stored.proposal_digest):
            raise CapabilityApprovalConflict("proposal_definition_unbound")
        # The digest covers the capability's current definition, so a changed
        # definition fails here like a changed request or a tampered row.
        digest = proposal_digest(_bound_payload(expected_payload, stored.capability_id))
        if (not hmac.compare_digest(digest, stored.proposal_digest)
                or not hmac.compare_digest(
                    proposal_digest(_bound_payload(stored.payload, stored.capability_id)), stored.proposal_digest,
                )):
            raise CapabilityApprovalConflict("proposal_digest_mismatch")
        if stored.capability_id != str(expected_payload.get("capability_id", "")):
            raise CapabilityApprovalConflict("proposal_capability_mismatch")
        row = self.connection.execute(
            f"""UPDATE omnix_capability_approvals
                   SET decision = 'consumed', consumed_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND id = %s AND proposal_digest = %s
                   AND expires_at > clock_timestamp()
                   AND (decision = 'approved' OR (decision = 'pending' AND NOT approval_required))
                 RETURNING {_COLUMNS}""",
            (context.workspace_id, identifier, digest),
        ).fetchone()
        if row is None:
            raise CapabilityApprovalConflict("proposal_not_approved_expired_or_consumed")
        self.connection.execute(
            """INSERT INTO omnix_module_records (
                workspace_id, module, record_type, record_id, owner_user_id, payload
            ) VALUES (%s, 'assistant-tools', 'execution-ledger', %s, %s, %s::jsonb)""",
            (context.workspace_id, str(ledger_payload["execution_id"]), context.user_id,
             json.dumps(ledger_payload, ensure_ascii=False, allow_nan=False)),
        )
        return self._record(row)

    def open_count(self, capability_ids: list[str]) -> int:
        """Pending or approved, unconsumed proposals for these capabilities in every workspace the session sees."""
        row = self.connection.execute(
            """SELECT count(*) FROM omnix_capability_approvals
                WHERE capability_id = ANY(%s) AND decision IN ('pending', 'approved')""",
            (sorted(set(capability_ids)),),
        ).fetchone()
        return int(row[0])

    def expire_for_capabilities(self, context: TenantContext, capability_ids: list[str], *, reason: str) -> int:
        """Expire this workspace's open proposals for these capabilities, approved or not (PA-4.3: a retired tool)."""
        if not capability_ids:
            return 0
        cursor = self.connection.execute(
            """UPDATE omnix_capability_approvals
                  SET decision = 'expired', reason = %s, decided_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND capability_id = ANY(%s) AND decision IN ('pending', 'approved')""",
            (reason, context.workspace_id, sorted(set(capability_ids))),
        )
        return int(cursor.rowcount)
