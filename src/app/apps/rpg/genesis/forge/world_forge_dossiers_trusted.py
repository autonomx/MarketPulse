"""Trusted dossier projection that never invents fallback lore prose."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .world_forge_dossiers import (
    DOSSIER_SCHEMA_VERSION,
    project_entity_dossier as _legacy_project_entity_dossier,
    text,
)


def _has_authored_sections(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    sections = value.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        return False
    return any(
        isinstance(section, Mapping)
        and isinstance(section.get("paragraphs"), Sequence)
        and not isinstance(section.get("paragraphs"), (str, bytes))
        and any(str(paragraph or "").strip() for paragraph in section.get("paragraphs") or ())
        for section in sections
    )


def project_entity_dossier(
    row: Mapping[str, Any],
    *,
    card_type: str,
    content: Mapping[str, Any] | None = None,
    entity_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """Normalize an authored dossier or return an explicit generation-required shell.

    Structured values remain available through card highlights and quick-fact tables.
    They are never converted into dossier paragraphs or generic overview sentences.
    """

    source = dict(row)
    raw_dossier = source.get("dossier")
    if _has_authored_sections(raw_dossier):
        short_summary, dossier = _legacy_project_entity_dossier(
            source,
            card_type=card_type,
            content=dict(content or {}),
            entity_id=entity_id,
        )
        dossier["generated_from_legacy"] = False
        dossier["lore_required"] = False
        return short_summary, dossier

    explicit_summary = text(source.get("short_summary"))
    return explicit_summary, {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "subtitle": "",
        "quote": None,
        "quick_facts": [],
        "sections": [],
        "related_entity_ids": [],
        "generated_from_legacy": True,
        "lore_required": True,
        "dossier_status": "generation_required",
    }


__all__ = ["project_entity_dossier"]
