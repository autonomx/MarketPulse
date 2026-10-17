"""Project structured published-world canon into player-safe Campaign Bible pages."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value or () if isinstance(row, Mapping)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "entry"


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _render_value(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, nested in value.items():
            rendered = _render_value(nested, depth=depth + 1)
            if not rendered:
                continue
            if "\n" in rendered:
                lines.append(f"{_label(str(key))}:\n{rendered}")
            else:
                lines.append(f"{_label(str(key))}: {rendered}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        parts = [_render_value(item, depth=depth + 1) for item in value]
        parts = [item for item in parts if item]
        return "\n".join(f"• {item}" for item in parts)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _text(value)


def _page(
    *,
    document_id: str,
    topic_id: str,
    title: str,
    value: Any,
    canon_revision: int,
) -> dict[str, Any] | None:
    full_text = _render_value(value)
    if not full_text:
        return None
    return {
        "document_id": document_id,
        "topic_id": topic_id,
        "title": title,
        "full_text": full_text,
        "summary_500": full_text[:500].rstrip(),
        "summary_120": full_text[:120].rstrip(),
        "keywords": [_slug(title), topic_id],
        "entity_refs": [],
        "visibility": "public",
        "canon_revision": canon_revision,
        "provenance": {"source": "published_structured_world_canon"},
    }


def project_published_canon(
    canon: Mapping[str, Any],
    *,
    campaign_id: str,
    canon_revision: int,
) -> dict[str, Any]:
    """Return a Campaign Bible, compiling legacy structured canon when needed."""

    bible = deepcopy(dict(canon))
    bible["campaign_id"] = campaign_id
    bible["canon_revision"] = int(bible.get("canon_revision") or canon_revision or 1)
    existing_documents = _rows(bible.get("documents"))
    if existing_documents:
        return bible

    revision = int(bible["canon_revision"])
    pages: list[dict[str, Any]] = []

    def add(document: dict[str, Any] | None) -> None:
        if document is not None:
            pages.append(document)

    summary = _text(bible.get("summary"))
    themes = [_text(item) for item in bible.get("themes") or () if _text(item)]
    overview = summary
    if themes:
        overview += ("\n\nThemes:\n" if overview else "Themes:\n") + "\n".join(
            f"• {theme}" for theme in themes
        )
    add(
        _page(
            document_id="lore:published:world-overview",
            topic_id="realm",
            title="World Overview",
            value=overview,
            canon_revision=revision,
        )
    )
    for key, topic_id, title in (
        ("isekai_premise", "realm", "Earthborn and the Meridian Gates"),
        ("cosmology", "cosmology", "Cosmology"),
        ("magic", "magic_technology", _text(_mapping(bible.get("magic")).get("name")) or "Magic"),
        ("economy", "economy", "Economy and Trade"),
    ):
        add(
            _page(
                document_id=f"lore:published:{_slug(key)}",
                topic_id=topic_id,
                title=title,
                value=bible.get(key),
                canon_revision=revision,
            )
        )

    history = _rows(bible.get("history"))
    add(
        _page(
            document_id="lore:published:history",
            topic_id="history",
            title="History of the World",
            value=history,
            canon_revision=revision,
        )
    )

    entities = deepcopy(_mapping(bible.get("entities")))
    for key, topic_id, kind, title_field in (
        ("regions", "regions", "region", "name"),
        ("factions", "factions", "faction", "name"),
        ("bestiary", "monsters", "monster", "name"),
    ):
        for index, row in enumerate(_rows(bible.get(key)), start=1):
            entry_id = _text(row.get("id")) or f"{kind}:{index}"
            title = _text(row.get(title_field)) or _label(entry_id.split(":")[-1])
            safe_row = {field: value for field, value in row.items() if field != "secrets"}
            add(
                _page(
                    document_id=f"lore:published:{_slug(entry_id)}",
                    topic_id=topic_id,
                    title=title,
                    value=safe_row,
                    canon_revision=revision,
                )
            )
            if kind == "faction":
                entities[entry_id] = {
                    "kind": "faction",
                    "name": title,
                    "public_goal": _text(row.get("agenda")),
                    "description": _text(row.get("stance_to_earthborn")),
                    "visibility": "public",
                }

    discovery = deepcopy(_mapping(bible.get("discovery_state")))
    page_status = deepcopy(_mapping(discovery.get("pages")))
    for document in pages:
        page_status[str(document["document_id"])] = "public_at_campaign_start"
    entity_status = deepcopy(_mapping(discovery.get("entities")))
    for entity_id, entity in entities.items():
        if _text(_mapping(entity).get("visibility")) == "public":
            entity_status[str(entity_id)] = "public_at_campaign_start"
    discovery.update(
        {
            "pages": page_status,
            "entities": entity_status,
            "discoveries": list(discovery.get("discoveries") or ()),
        }
    )
    bible.update(
        {
            "schema_version": _text(bible.get("schema_version")) or "rpg_campaign_bible_v2",
            "documents": pages,
            "entities": entities,
            "discovery_state": discovery,
        }
    )
    return bible
