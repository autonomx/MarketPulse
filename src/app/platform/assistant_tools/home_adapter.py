"""Semantic Home adapter, initially backed by TP-Link Kasa."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .kasa_adapter import KasaRuntimeAdapter, PythonKasaRuntimeAdapter
from .models import AssistantToolRequest, AssistantToolResult


def run_home_tool_request(request: AssistantToolRequest, adapter: KasaRuntimeAdapter | None = None) -> AssistantToolResult:
    runtime = adapter or PythonKasaRuntimeAdapter()
    target = str(request.input.get("target") or "").strip()
    try:
        if request.action_id == "home.list_devices":
            devices = runtime.discover_devices()
            return _result(request, "low", False, f"Discovered {len(devices)} home device(s).", {"devices": [_semantic(item) for item in devices]})
        if request.action_id == "home.get_state":
            device = runtime.get_state(target=target)
            row = _semantic(device)
            return _result(request, "low", False, f"{row['name']} is {'on' if row['on'] else 'off'}.", {"device": row})
        if request.action_id == "home.set_state":
            desired = _desired_state(request.input)
            before, after = runtime.set_state(target=target, on=desired)
            return _result(request, "medium", before.is_on != after.is_on, f"Verified {after.alias} is {'on' if after.is_on else 'off'}.", {"before": _semantic(before), "after": _semantic(after), "verified": True})
        if request.action_id == "home.apply_scene":
            actions = request.input.get("actions")
            if not isinstance(actions, list) or not actions:
                raise ValueError("scene actions are required")
            results, changed = [], False
            for row in actions[:20]:
                if not isinstance(row, dict):
                    raise ValueError("scene action must be an object")
                desired = _desired_state(row)
                before, after = runtime.set_state(target=str(row.get("target") or ""), on=desired)
                changed = changed or before.is_on != after.is_on
                results.append({"before": _semantic(before), "after": _semantic(after), "verified": after.is_on is desired})
            return _result(request, "high", changed, f"Applied and verified {len(results)} home scene action(s).", {"actions": results, "verified": all(item["verified"] for item in results)})
        if request.action_id == "home.get_energy":
            return _result(request, "low", False, "Energy telemetry is not exposed by the current Home adapter.", {"supported": False, "adapter": "kasa"}, error="home_energy_not_supported")
        return _result(request, "low", False, "Home action is not available.", {}, error="home_action_not_available")
    except Exception as exc:
        return _result(request, "medium" if request.action_id in {"home.set_state", "home.apply_scene"} else "low", False, "Home action failed.", {}, error=str(exc)[:500])


def _semantic(device: Any) -> dict[str, object]:
    row = asdict(device)
    return {
        "id": row.get("device_id") or row.get("host"),
        "name": row.get("alias") or row.get("host"),
        "kind": "switch",
        "on": bool(row.get("is_on")),
        "provider": "kasa",
        "provider_metadata": {"host": row.get("host"), "model": row.get("model"), "rssi": row.get("rssi")},
    }


def _desired_state(data: dict[str, Any]) -> bool:
    if isinstance(data.get("on"), bool):
        return bool(data["on"])
    value = str(data.get("state") or "").strip().casefold()
    if value in {"on", "true", "1"}:
        return True
    if value in {"off", "false", "0"}:
        return False
    raise ValueError("home state must provide boolean 'on' or state=on/off")


def _result(request: AssistantToolRequest, risk: str, changed: bool, summary: str, output: dict[str, object], *, error: str | None = None) -> AssistantToolResult:
    return AssistantToolResult(tool_id=request.tool_id, action_id=request.action_id, session_id=request.session_id, risk_level=risk, state_changed=changed, result_summary=summary, output=output, error=error)
