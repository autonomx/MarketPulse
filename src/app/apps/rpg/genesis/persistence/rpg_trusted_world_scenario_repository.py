"""Trusted-authorship persistence boundary for reusable RPG world topics."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from app.apps.rpg.genesis.worlds.generation_authorship import AuthorshipValidationError
from app.apps.rpg.genesis.worlds.generation_authorship_policy_signing import (
    bind_signed_authorship_policy,
    require_policy_bound_authorship,
    signed_authorship_policy,
)
from app.apps.rpg.genesis.worlds.generation_authorship_signing import (
    attach_signed_human_authorship,
    sanitize_untrusted_candidate,
)
from app.apps.rpg.genesis.worlds.generation_test_mode import deterministic_world_forge_test_mode

from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.apps.rpg.foundation.persistence.rpg_world_scenario_repository import PostgresRpgWorldScenarioRepository
from app.persistence.tenant import TenantContext


def _hash(value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _fixture_exempt(payload: Mapping[str, Any]) -> bool:
    if not deterministic_world_forge_test_mode():
        return False
    provenance = payload.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    return bool(provenance.get("deterministic_fixture_only")) or str(
        provenance.get("generator") or ""
    ).startswith("deterministic_")


def _has_llm_lineage(payload: Mapping[str, Any]) -> bool:
    provenance = payload.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    authorship = provenance.get("authorship")
    authorship = dict(authorship) if isinstance(authorship, Mapping) else {}
    artifacts = authorship.get("generation_artifacts")
    return isinstance(artifacts, Mapping) and bool(artifacts)


class PostgresTrustedRpgWorldScenarioRepository(PostgresRpgWorldScenarioRepository):
    """Create human evidence server-side and reject unsigned ready AI content."""

    def _current_topic_content(
        self,
        context: TenantContext,
        world_id: str,
        topic_id: str,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT content_jsonb FROM omnix_rpg_world_topics "
            "WHERE workspace_id = %s AND world_id = %s AND topic_id = %s",
            (context.workspace_id, world_id, topic_id),
        ).fetchone()
        return dict(row[0]) if row is not None else {}

    def put_topic(
        self,
        context: TenantContext,
        *,
        world_id: str,
        topic_id: str,
        draft_revision: int,
        source: str,
        status: str,
        content: Mapping[str, Any],
        directives: Mapping[str, Any] | None = None,
        dependency_hashes: Mapping[str, str] | None = None,
        input_hash: str = "",
        content_hash: str = "",
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(content)
        row_provenance = {
            key: value
            for key, value in dict(provenance or {}).items()
            if key not in {"authorship", "test_authorship_exemption"}
        }

        if source == "manual":
            try:
                require_policy_bound_authorship(payload)
            except AuthorshipValidationError:
                prior = self._current_topic_content(context, world_id, topic_id)
                policy = signed_authorship_policy(prior)
                event_id = (
                    f"humanedit:{world_id}:{topic_id}:{draft_revision}:"
                    f"{datetime.now(timezone.utc).isoformat()}"
                )
                payload = attach_signed_human_authorship(
                    sanitize_untrusted_candidate(payload),
                    event_id=event_id,
                    prior_candidate=prior,
                    edited_llm=_has_llm_lineage(prior),
                    policy=policy,
                )
                payload = bind_signed_authorship_policy(payload, policy)
                require_policy_bound_authorship(payload)
            content_hash = _hash(payload)
        elif source == "ai" and status == "ready":
            if not _fixture_exempt(payload):
                require_policy_bound_authorship(payload)
            content_hash = _hash(payload)

        embedded = payload.get("provenance")
        embedded = dict(embedded) if isinstance(embedded, Mapping) else {}
        if "authorship" in embedded:
            row_provenance["authorship"] = embedded["authorship"]

        return super().put_topic(
            context,
            world_id=world_id,
            topic_id=topic_id,
            draft_revision=draft_revision,
            source=source,
            status=status,
            content=payload,
            directives=directives,
            dependency_hashes=dependency_hashes,
            input_hash=input_hash,
            content_hash=content_hash or _hash(payload),
            provenance=row_provenance,
        )


__all__ = ["PostgresTrustedRpgWorldScenarioRepository"]
