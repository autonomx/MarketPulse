"""Central limits, privacy, and retention policy for web research."""
from __future__ import annotations

from app.config.env import environment

import os
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    search_cache_ttl_seconds: int = 300
    extraction_cache_ttl_seconds: int = 3_600
    raw_snapshot_retention_days: int = 7
    source_manifest_retention_days: int = 30
    planner_receives_conversation_history: bool = False
    synthesis_receives_raw_page_bodies: bool = False


def research_policy_from_env() -> ResearchPolicy:
    return ResearchPolicy(
        search_cache_ttl_seconds=_env_int("OMNIX_RESEARCH_SEARCH_CACHE_TTL_SECONDS", 300, 1, 86_400),
        extraction_cache_ttl_seconds=_env_int(
            "OMNIX_RESEARCH_EXTRACTION_CACHE_TTL_SECONDS", 3_600, 1, 604_800
        ),
        raw_snapshot_retention_days=_env_int(
            "OMNIX_RESEARCH_RAW_RETENTION_DAYS", 7, 0, 365
        ),
        source_manifest_retention_days=_env_int(
            "OMNIX_RESEARCH_MANIFEST_RETENTION_DAYS", 30, 1, 3_650
        ),
    )


def privacy_contract(policy: ResearchPolicy | None = None) -> dict[str, object]:
    resolved = policy or research_policy_from_env()
    return {
        "planner_input": ["question", "budgets", "source_preferences", "compact_evidence_summary"],
        "planner_receives_conversation_history": resolved.planner_receives_conversation_history,
        "synthesis_input": ["question", "objective", "structured_evidence", "source_metadata", "conflicts", "limitations"],
        "synthesis_receives_raw_page_bodies": resolved.synthesis_receives_raw_page_bodies,
        "credentials_browser_readable": False,
        "unrelated_connected_data_included": False,
    }


def expiry_iso(days: int, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return datetime.fromtimestamp(
        current.timestamp() + max(0, days) * 86_400,
        tz=timezone.utc,
    ).isoformat()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(environment().get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
