"""Assist mode chat turns: plan with Hermes or locally, then apply the result (readouts, house tools, review)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.platform.chat.assist.hermes import HermesAssistantPlanner
from app.platform.chat.assist.house import apply_house_mock, infer_house_plan
from app.platform.chat.assist.models import AssistantResult, ToolResult, AssistantRequest
from app.platform.chat.assist.review import hold_for_review, review_call
from app.providers.hermes_status import hermes_runtime_config


READOUT_NAMES = {
    "get_house_status",
    "get_hermes_status",
    "get_hermes_diagnostics_schema",
}


def _contributed_readouts() -> dict[str, Any]:
    from app.platform.chat.contracts import ASSIST_READOUTS
    from app.runtime.ports import implementations

    return {readout.name: readout for readout in implementations(ASSIST_READOUTS)}


def readout_payload(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = str(name or "").strip()
    _ = args or {}
    if clean == "get_house_status":
        from app.platform.chat.assist.house import load_house_state

        return {"ok": True, "name": clean, "payload": {"state": load_house_state()}}
    if clean == "get_hermes_status":
        from app.providers.hermes_status import hermes_status_payload

        return {"ok": True, "name": clean, "payload": hermes_status_payload()}
    if clean == "get_hermes_diagnostics_schema":
        from app.platform.chat.assist.diagnostics import hermes_diagnostics_schema

        return {"ok": True, "name": clean, "payload": hermes_diagnostics_schema()}
    contributed = _contributed_readouts().get(clean)
    if contributed is not None:
        return {"ok": True, "name": clean, "payload": contributed.payload(_)}
    return {"ok": False, "name": clean, "error": "unknown_readout"}


def apply_mode_result(result: AssistantResult, *, dry_run: bool) -> AssistantResult:
    if not result.tool_calls:
        return result
    rows: list[ToolResult] = []
    for call in result.tool_calls:
        if call.name in READOUT_NAMES or call.name in _contributed_readouts():
            row = readout_payload(call.name, call.args)
            rows.append(ToolResult(name=call.name, ok=bool(row.get("ok")), output=row, executed=False, error=str(row.get("error")) if row.get("error") else None))
            continue
        if result.domain != "house":
            continue
        decision = review_call(call)
        if decision.requires_confirmation and not dry_run:
            return hold_for_review(result, call, dry_run=dry_run)
        try:
            rows.append(apply_house_mock(call, dry_run=dry_run))
        except Exception as exc:
            rows.append(ToolResult(name=call.name, ok=False, error=str(exc), executed=False))
    if not rows:
        return result
    result.tool_results = rows
    result.success = all(item.ok for item in rows)
    if result.success and rows and dry_run:
        result.response = f"Dry run: {result.response}"
    elif result.success and rows:
        result.response = describe_result(rows[-1], fallback=result.response)
    return result


def describe_result(row: ToolResult, *, fallback: str) -> str:
    if row.name == "set_light":
        room = str(row.output.get("room", "room")).replace("_", " ")
        state = str(row.output.get("state", "updated"))
        return f"{room.title()} lights are {state}."
    if row.name == "set_brightness":
        room = str(row.output.get("room", "room")).replace("_", " ")
        brightness = row.output.get("brightness", "updated")
        return f"{room.title()} brightness is set to {brightness}%."
    return fallback


@dataclass
class ModeChatRequest:
    content: str
    session_id: str = "default"
    domain: str = "chat"
    dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModeChatResponse:
    ok: bool
    mode: str
    backend: str
    result: dict[str, Any]
    error: str | None = None


def detect_mode_domain(message: str) -> str:
    text = message.lower()
    if any(token in text for token in ("light", "brightness", "thermostat", "house status")):
        return "house"
    if "podcast" in text:
        return "podcast"
    if any(token in text for token in ("rpg", "quest", "npc", "scene", "bran")):
        return "rpg"
    return "chat"


def local_mode_plan(request: AssistantRequest) -> AssistantResult:
    if request.domain == "house":
        return apply_mode_result(infer_house_plan(request), dry_run=request.dry_run)
    return AssistantResult(success=True, response="Agent mode is ready. No tool plan is needed for this message.", domain=request.domain)


def plan_mode_chat(request: ModeChatRequest) -> ModeChatResponse:
    domain = request.domain if request.domain != "chat" else detect_mode_domain(request.content)
    assistant_request = AssistantRequest(
        message=request.content,
        session_id=request.session_id,
        domain=domain,
        dry_run=request.dry_run,
        metadata=request.metadata,
    )
    config = hermes_runtime_config()
    if config.enabled:
        try:
            result = HermesAssistantPlanner(base_url=config.base_url, timeout=config.timeout_seconds).plan(assistant_request)
            result = apply_mode_result(result, dry_run=request.dry_run)
            return ModeChatResponse(ok=result.success, mode="agent", backend="hermes", result=asdict(result))
        except Exception as exc:
            result = local_mode_plan(assistant_request)
            return ModeChatResponse(ok=result.success, mode="agent", backend="local_fallback", result=asdict(result), error=str(exc))
    result = local_mode_plan(assistant_request)
    return ModeChatResponse(ok=result.success, mode="agent", backend="local", result=asdict(result))
