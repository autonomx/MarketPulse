"""Cross-topic causal consistency checks for generated World Forge canon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .world_forge_generation import GeneratedTopic

_TERMINAL_LEGACY_STATUSES = frozenset(
    {"terminated", "reversed", "absorbed", "concealed", "forgotten"}
)
_FORMATION_FIELDS = {
    "regions": "formation_event_ids",
    "places": "founding_event_ids",
    "groups": "formation_event_ids",
    "cultures": "origin_event_ids",
    "actors": "formative_event_ids",
}
_FORMATION_EFFECT_TYPES = frozenset(
    {"founded", "formed", "created", "fragmented", "culturally_influenced"}
)


@dataclass(frozen=True)
class CausalAuditFinding:
    code: str
    message: str
    item_id: str = ""
    severity: str = "error"


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("entity_id") or "").strip()


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _topic_entities(
    topics: Iterable[GeneratedTopic],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, tuple[Mapping[str, Any], ...]],
    dict[str, str],
]:
    entities: dict[str, Mapping[str, Any]] = {}
    by_topic: dict[str, tuple[Mapping[str, Any], ...]] = {}
    topic_by_entity: dict[str, str] = {}
    for topic in topics:
        rows = tuple(topic.entities)
        by_topic[topic.topic_id] = rows
        for row in rows:
            entity_id = _entity_id(row)
            if entity_id:
                entities[entity_id] = row
                topic_by_entity[entity_id] = topic.topic_id
    return entities, by_topic, topic_by_entity


def _event_year(row: Mapping[str, Any]) -> int | None:
    for field in ("start_year", "year", "date_year", "end_year"):
        resolved = _integer(row.get(field))
        if resolved is not None:
            return resolved
    return None


def _causal_cycles(edges: Mapping[str, set[str]]) -> set[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def walk(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            if node in path:
                cyclic.update(path[path.index(node) :])
            cyclic.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in edges.get(node, set()):
            walk(dependency, (*path, node))
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        walk(node, ())
    return cyclic


def audit_causal_canon(
    topics: Iterable[GeneratedTopic],
) -> tuple[CausalAuditFinding, ...]:
    topic_rows = tuple(topics)
    entities, by_topic, topic_by_entity = _topic_entities(topic_rows)
    history = {
        _entity_id(row): row
        for row in by_topic.get("history_timeline", ())
        if _entity_id(row)
    }
    has_causal_links_topic = "causal_links" in by_topic
    links = tuple(by_topic.get("causal_links", ()))
    findings: list[CausalAuditFinding] = []
    event_edges: dict[str, set[str]] = {}
    linked_events: set[str] = set()
    persistences_by_event: dict[str, set[str]] = {}
    link_signatures: set[tuple[str, str, str]] = set()
    formation_signatures: set[tuple[str, str]] = set()

    for event_id, event in history.items():
        causes = _strings(event.get("cause_event_ids"))
        event_edges[event_id] = set(causes)
        for cause_id in causes:
            if cause_id == event_id:
                findings.append(
                    CausalAuditFinding(
                        "historical_self_causation",
                        "A historical event cannot list itself as a cause.",
                        event_id,
                    )
                )
                continue
            cause = history.get(cause_id)
            if cause is None:
                findings.append(
                    CausalAuditFinding(
                        "unknown_causal_event",
                        f"Historical cause does not resolve: {cause_id}",
                        event_id,
                    )
                )
                continue
            cause_year = _event_year(cause)
            event_year = _event_year(event)
            if cause_year is not None and event_year is not None and cause_year > event_year:
                findings.append(
                    CausalAuditFinding(
                        "historical_cause_after_effect",
                        f"Cause year {cause_year} follows effect year {event_year}.",
                        event_id,
                    )
                )

    for event_id in sorted(_causal_cycles(event_edges)):
        findings.append(
            CausalAuditFinding(
                "historical_causal_cycle",
                "Historical event causation contains a cycle.",
                event_id,
            )
        )

    for index, link in enumerate(links, start=1):
        link_id = _entity_id(link) or f"causal_link:{index}"
        causes = _strings(link.get("cause_event_ids"))
        effect_id = str(link.get("effect_id") or "").strip()
        effect_type = str(link.get("effect_type") or "").strip()
        persistence = str(link.get("persistence") or "").strip()
        mechanism = str(link.get("mechanism") or "").strip()
        start_year = _integer(link.get("start_year"))
        end_year = _integer(link.get("end_year"))

        if not mechanism or len(mechanism.split()) < 4:
            findings.append(
                CausalAuditFinding(
                    "missing_causal_mechanism",
                    "Causal links require a concrete multi-word mechanism.",
                    link_id,
                )
            )
        if start_year is not None and end_year is not None and end_year < start_year:
            findings.append(
                CausalAuditFinding(
                    "causal_date_reversal",
                    f"Causal link end year {end_year} precedes start year {start_year}.",
                    link_id,
                )
            )
        effect = entities.get(effect_id)
        if not effect_id or effect is None:
            findings.append(
                CausalAuditFinding(
                    "unknown_causal_effect",
                    f"Causal effect does not resolve: {effect_id or '<missing>'}",
                    link_id,
                )
            )

        effect_topic = topic_by_entity.get(effect_id, "")
        origin_field = _FORMATION_FIELDS.get(effect_topic)
        origin_contract_present = bool(effect and origin_field and origin_field in effect)
        declared_origins = (
            set(_strings(effect.get(origin_field)))
            if effect and origin_field and origin_contract_present
            else set()
        )

        for cause_id in causes:
            if cause_id not in history:
                findings.append(
                    CausalAuditFinding(
                        "unknown_causal_event",
                        f"Causal link event does not resolve: {cause_id}",
                        link_id,
                    )
                )
                continue
            linked_events.add(cause_id)
            persistences_by_event.setdefault(cause_id, set()).add(persistence)
            signature = (cause_id, effect_id, effect_type)
            if signature in link_signatures:
                findings.append(
                    CausalAuditFinding(
                        "causal_link_duplicate_effect",
                        "Duplicate event, effect, and effect-type causal link.",
                        link_id,
                    )
                )
            link_signatures.add(signature)
            if effect_type in _FORMATION_EFFECT_TYPES:
                formation_signatures.add((cause_id, effect_id))
                if origin_contract_present and cause_id not in declared_origins:
                    findings.append(
                        CausalAuditFinding(
                            "causal_link_conflicts_with_entity_origin",
                            f"Formation link names {cause_id}, but {effect_topic}.{origin_field} does not.",
                            link_id,
                        )
                    )

    for event_id, event in history.items():
        status = str(event.get("legacy_status") or "").strip()
        legacies = event.get("present_day_legacies")
        persistences = persistences_by_event.get(event_id, set())
        continuing = "continuing" in persistences
        terminal = any(value and value != "continuing" for value in persistences)
        if (
            has_causal_links_topic
            and event_id not in linked_events
            and status not in _TERMINAL_LEGACY_STATUSES
        ):
            findings.append(
                CausalAuditFinding(
                    "historical_event_without_legacy_resolution",
                    "A major historical event needs a causal link or explicit terminal legacy status.",
                    event_id,
                )
            )
        if status == "continuing":
            if legacies in (None, "", [], (), {}):
                findings.append(
                    CausalAuditFinding(
                        "historical_event_without_legacy_resolution",
                        "A continuing historical event requires present-day legacies.",
                        event_id,
                    )
                )
            if has_causal_links_topic and not continuing:
                findings.append(
                    CausalAuditFinding(
                        "historical_legacy_persistence_mismatch",
                        "A continuing historical event requires at least one continuing causal effect.",
                        event_id,
                    )
                )
        elif status == "mixed":
            if legacies in (None, "", [], (), {}) or not continuing or not terminal:
                findings.append(
                    CausalAuditFinding(
                        "historical_legacy_persistence_mismatch",
                        "A mixed historical legacy requires continuing and resolved effects plus a present-day trace.",
                        event_id,
                    )
                )
        elif status in _TERMINAL_LEGACY_STATUSES and continuing:
            findings.append(
                CausalAuditFinding(
                    "historical_legacy_persistence_mismatch",
                    f"Legacy status {status} conflicts with a continuing causal effect.",
                    event_id,
                )
            )

    for topic_id, field_id in _FORMATION_FIELDS.items():
        for entity in by_topic.get(topic_id, ()):
            entity_id = _entity_id(entity)
            for event_id in _strings(entity.get(field_id)):
                if event_id not in history:
                    findings.append(
                        CausalAuditFinding(
                            "unknown_causal_event",
                            f"Entity origin event does not resolve: {event_id}",
                            entity_id,
                        )
                    )
                if (
                    has_causal_links_topic
                    and (event_id, entity_id) not in formation_signatures
                ):
                    findings.append(
                        CausalAuditFinding(
                            "formation_event_without_causal_link",
                            f"{field_id} lacks a matching formation causal link.",
                            entity_id,
                        )
                    )

    return tuple(findings)


__all__ = ["CausalAuditFinding", "audit_causal_canon"]
