"""Rollout metrics for causal World Forge generation quality."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from .canon_audit import CanonAuditReport
from .world_forge_generation import GeneratedTopic

_TERMINAL = frozenset({"terminated", "reversed", "absorbed", "concealed", "forgotten"})
_FORMATION_FIELDS = {
    "regions": "formation_event_ids",
    "places": "founding_event_ids",
    "groups": "formation_event_ids",
    "cultures": "origin_event_ids",
    "actors": "formative_event_ids",
}
_FORMATION_EFFECTS = frozenset(
    {"founded", "formed", "created", "fragmented", "culturally_influenced"}
)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("entity_id") or "").strip()


def _basis_points(numerator: int, denominator: int) -> int:
    return round(10000 * numerator / denominator) if denominator else 0


def evaluate_causal_generation(
    topics: Iterable[GeneratedTopic],
) -> dict[str, int]:
    topic_map = {topic.topic_id: topic for topic in topics}
    history = tuple(topic_map.get("history_timeline", GeneratedTopic("history_timeline")).entities)
    links_topic = topic_map.get("causal_links")
    links = tuple(links_topic.entities) if links_topic is not None else ()
    linked_events = {
        event_id
        for link in links
        for event_id in _strings(link.get("cause_event_ids"))
    }
    mechanisms = {
        " ".join(str(link.get("mechanism") or "").casefold().split())
        for link in links
        if str(link.get("mechanism") or "").strip()
    }
    present_trace_events = sum(
        1
        for event in history
        if event.get("present_day_legacies") not in (None, "", [], (), {})
    )
    terminal_events = sum(
        1
        for event in history
        if str(event.get("legacy_status") or "").strip() in _TERMINAL
    )
    formation_references: set[tuple[str, str]] = set()
    for topic_id, field_id in _FORMATION_FIELDS.items():
        topic = topic_map.get(topic_id)
        if topic is None:
            continue
        for entity in topic.entities:
            effect_id = _entity_id(entity)
            formation_references.update(
                (event_id, effect_id)
                for event_id in _strings(entity.get(field_id))
            )
    formation_links = {
        (event_id, str(link.get("effect_id") or "").strip())
        for link in links
        if str(link.get("effect_type") or "").strip() in _FORMATION_EFFECTS
        for event_id in _strings(link.get("cause_event_ids"))
    }
    matched_formations = len(formation_references & formation_links)
    applicable = int(links_topic is not None)
    return {
        "causal_evaluation_applicable": applicable,
        "causal_history_events": len(history),
        "causal_links": len(links),
        "causal_linked_events": len(linked_events),
        "causal_event_coverage_bps": _basis_points(len(linked_events), len(history)),
        "causal_present_trace_events": present_trace_events,
        "causal_terminal_events": terminal_events,
        "causal_unique_mechanisms": len(mechanisms),
        "causal_mechanism_diversity_bps": _basis_points(len(mechanisms), len(links)),
        "causal_formation_references": len(formation_references),
        "causal_formation_matches": matched_formations,
        "causal_formation_coverage_bps": _basis_points(
            matched_formations,
            len(formation_references),
        ),
    }


def attach_causal_evaluation(
    topics: Iterable[GeneratedTopic],
    report: CanonAuditReport,
) -> CanonAuditReport:
    topic_list = tuple(topics)
    metrics = evaluate_causal_generation(topic_list)
    causal_errors = sum(
        1
        for issue in report.issues
        if issue.severity == "error"
        and (
            issue.code.startswith("causal_")
            or issue.code.startswith("historical_")
            or issue.code.startswith("formation_event_")
            or issue.code.startswith("unknown_causal_")
        )
    )
    applicable = metrics["causal_evaluation_applicable"] == 1
    promotion_ready = int(
        applicable
        and metrics["causal_links"] > 0
        and causal_errors == 0
        and metrics["causal_event_coverage_bps"] >= 8000
        and metrics["causal_mechanism_diversity_bps"] >= 7000
        and (
            metrics["causal_formation_references"] == 0
            or metrics["causal_formation_coverage_bps"] == 10000
        )
    )
    return replace(
        report,
        checks={
            **dict(report.checks),
            **metrics,
            "causal_errors": causal_errors,
            "causal_promotion_ready": promotion_ready,
        },
    )


__all__ = ["attach_causal_evaluation", "evaluate_causal_generation"]
