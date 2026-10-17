"""The mock house: its state (PostgreSQL or a local document), tool plan and tool execution."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.platform.chat.assist.models import ActionLogEntry, ConfirmationRequest, ToolCall, ToolResult, AssistantRequest, AssistantResult, ToolRiskLevel
from app.persistence.document_schemas import register_document_schema
from app.persistence.document_store import PostgresDocumentStore
from app.runtime.paths import resources_data_root


DEFAULT_HOUSE_STATE = {
    "rooms": {
        "kitchen": {"lights": "off", "brightness": 100},
        "living_room": {"lights": "off", "brightness": 100},
        "bedroom": {"lights": "off", "brightness": 100},
        "office": {"lights": "off", "brightness": 100},
    },
    "thermostat": {"temperature_c": 21},
    "reminders": [],
}


def assist_data_root() -> Path:
    path = resources_data_root() / "assist_core"
    path.mkdir(parents=True, exist_ok=True)
    return path


def house_state_path() -> Path:
    return assist_data_root() / "mock_house_state.json"


def load_house_state() -> dict[str, Any]:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        return load_house_state_postgres()
    path = house_state_path()
    if not path.exists():
        save_house_state(DEFAULT_HOUSE_STATE)
        return json.loads(json.dumps(DEFAULT_HOUSE_STATE))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEFAULT_HOUSE_STATE))


def save_house_state(state: dict[str, Any]) -> None:
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        return save_house_state_postgres(state)
    house_state_path().write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def normalize_room(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def require_room(state: dict[str, Any], room_name: str) -> dict[str, Any]:
    room_key = normalize_room(room_name)
    rooms = state.setdefault("rooms", {})
    if room_key not in rooms:
        raise ValueError(f"unknown_room:{room_key}")
    return rooms[room_key]


def load_house_state_postgres() -> dict[str, Any]:
    payload = PostgresDocumentStore().read(
        module="assist-core",
        record_type="house-state",
        default=DEFAULT_HOUSE_STATE,
    )
    return dict(payload) if isinstance(payload, dict) else dict(DEFAULT_HOUSE_STATE)


def save_house_state_postgres(payload: dict[str, Any]) -> None:
    PostgresDocumentStore().write(
        dict(payload),
        module="assist-core",
        record_type="house-state",
    )


class HouseStateDocument(BaseModel):
    """The ``assist-core/house-state`` document."""

    model_config = ConfigDict(extra="allow")

    rooms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    thermostat: dict[str, Any] = Field(default_factory=dict)
    reminders: list[Any] = Field(default_factory=list)


# Document shapes (WP-5.9).
register_document_schema("assist-core", "house-state", HouseStateDocument)
register_document_schema("assist-core", "pending-reviews", dict[str, ConfirmationRequest])
register_document_schema("assist-core", "action-log", ActionLogEntry)


def apply_house_mock(call: ToolCall, *, dry_run: bool = False) -> ToolResult:
    state = load_house_state()
    name = call.name
    args: dict[str, Any] = call.args

    if name == "get_house_status":
        return ToolResult(name=name, ok=True, output={"state": state}, executed=False)

    if name == "set_light":
        room_key = normalize_room(str(args.get("room", "")))
        target = str(args.get("state", "on")).strip().lower()
        if target not in {"on", "off"}:
            raise ValueError("invalid_light_state")
        room = require_room(state, room_key)
        if not dry_run:
            room["lights"] = target
            save_house_state(state)
        return ToolResult(name=name, ok=True, output={"room": room_key, "state": target}, executed=not dry_run)

    if name == "set_brightness":
        room_key = normalize_room(str(args.get("room", "")))
        brightness = int(args.get("brightness", 100))
        if not 0 <= brightness <= 100:
            raise ValueError("brightness_out_of_range")
        room = require_room(state, room_key)
        if not dry_run:
            room["brightness"] = brightness
            save_house_state(state)
        return ToolResult(name=name, ok=True, output={"room": room_key, "brightness": brightness}, executed=not dry_run)

    raise ValueError(f"unknown_house_mock:{name}")


ROOMS = ("kitchen", "living room", "living_room", "bedroom", "office")


def infer_house_plan(request: AssistantRequest) -> AssistantResult:
    text = request.message.strip().lower()
    room = find_room(text)

    if "status" in text:
        calls = [ToolCall(name="get_house_status", risk=ToolRiskLevel.LOW)]
        return AssistantResult(success=True, response="I can check the house status.", domain="house", tool_calls=calls)

    if "brightness" in text or "dim" in text:
        calls = [ToolCall(name="set_brightness", args={"room": room, "brightness": find_brightness(text)}, risk=ToolRiskLevel.LOW)]
        return AssistantResult(success=True, response="I can adjust the room brightness.", domain="house", tool_calls=calls)

    if "light" in text:
        desired = "off" if "off" in text else "on"
        calls = [ToolCall(name="set_light", args={"room": room, "state": desired}, risk=ToolRiskLevel.LOW)]
        return AssistantResult(success=True, response="I can update the room lights.", domain="house", tool_calls=calls)

    return AssistantResult(success=False, response="I could not map that to a house plan yet.", domain="house")


def find_room(text: str) -> str:
    for room in ROOMS:
        if room in text:
            return room.replace(" ", "_")
    return "living_room"


def find_brightness(text: str) -> int:
    match = re.search(r"(\d{1,3})", text)
    if match:
        return max(0, min(100, int(match.group(1))))
    return 30 if "dim" in text else 100
