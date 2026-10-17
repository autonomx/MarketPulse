"""Deterministic scene-change detection migrated from environmental narration logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .authority import BeatPurpose
from .contracts import SceneChange


@dataclass(frozen=True)
class SceneChangeReport:
    changes: tuple[SceneChange, ...]
    required_beat_purposes: tuple[BeatPurpose, ...]

    @property
    def scene_refresh_required(self) -> bool:
        return bool(self.changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_refresh_required": self.scene_refresh_required,
            "changes": [
                {
                    "kind": change.kind,
                    "importance": change.importance,
                    "evidence_refs": list(change.evidence_refs),
                    "metadata": dict(change.metadata),
                }
                for change in self.changes
            ],
            "required_beat_purposes": [purpose.value for purpose in self.required_beat_purposes],
        }


def _text(state: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = state.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _changed(previous: Mapping[str, Any], current: Mapping[str, Any], *keys: str) -> bool:
    before = _text(previous, *keys)
    after = _text(current, *keys)
    return bool(after and before != after)


def _append(
    changes: list[SceneChange],
    *,
    kind: str,
    importance: str,
    current: Mapping[str, Any],
    evidence_key: str | None = None,
) -> None:
    evidence_refs: tuple[str, ...] = ()
    if evidence_key:
        value = _text(current, evidence_key)
        if value:
            evidence_refs = (value,)
    changes.append(SceneChange(kind=kind, importance=importance, evidence_refs=evidence_refs))


def _explicit_first_turn(current: Mapping[str, Any]) -> bool:
    value = current.get("turn_index")
    if value is None:
        return False
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def detect_scene_changes(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> SceneChangeReport:
    before = dict(previous or {})
    after = dict(current or {})
    changes: list[SceneChange] = []

    is_new_game = not before or bool(after.get("new_game")) or _explicit_first_turn(after)
    if is_new_game:
        _append(changes, kind="new_game", importance="major", current=after, evidence_key="location_evidence_id")
    else:
        if _changed(before, after, "location_id", "location_name", "location"):
            _append(changes, kind="location_changed", importance="major", current=after, evidence_key="location_evidence_id")
        if _changed(before, after, "region_id", "region_name", "region"):
            _append(changes, kind="region_changed", importance="major", current=after, evidence_key="region_evidence_id")
        if _changed(before, after, "time_of_day", "world_time", "time"):
            _append(changes, kind="time_changed", importance="minor", current=after, evidence_key="time_evidence_id")
        if _changed(before, after, "weather_id", "weather"):
            _append(changes, kind="weather_changed", importance="notable", current=after, evidence_key="weather_evidence_id")
        if _changed(before, after, "major_event_id", "major_event"):
            _append(changes, kind="major_event", importance="major", current=after, evidence_key="event_evidence_id")
        if _changed(before, after, "activity_signature", "nearby_activity"):
            _append(changes, kind="nearby_activity_changed", importance="notable", current=after, evidence_key="activity_evidence_id")
        if _changed(before, after, "perception_signature", "visible_changes"):
            _append(changes, kind="perceptual_change", importance="notable", current=after, evidence_key="perception_evidence_id")
        if (
            bool(after.get("visited_before"))
            and _changed(before, after, "location_revision", "scene_revision")
            and not any(change.kind == "location_changed" for change in changes)
        ):
            _append(changes, kind="changed_return_visit", importance="major", current=after, evidence_key="location_evidence_id")

    required: list[BeatPurpose] = []
    kinds = {change.kind for change in changes}
    if kinds.intersection({"new_game", "location_changed", "region_changed", "changed_return_visit"}):
        required.append(BeatPurpose.SCENE_ESTABLISHMENT)
    if changes:
        required.append(BeatPurpose.ENVIRONMENTAL_CHANGE)
    return SceneChangeReport(
        changes=tuple(changes),
        required_beat_purposes=tuple(dict.fromkeys(required)),
    )
