"""Governed MCP tool execution backed by MCPorter.

Only operator-policy tools are callable.  Each invocation gets an ephemeral
MCPorter config containing exactly one approved server, and MCPorter project/user
config discovery is bypassed with ``--config`` + ``--root``.  OAuth is disabled
for agent calls; operators complete authentication out of band and expose only
explicit environment keys through the server policy.
"""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.capabilities.mcp_policy import McpServerPolicy, McpToolPolicy, resolve_mcp_tool

from .models import AssistantToolRequest, AssistantToolResult

_SAFE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)
_MAX_OUTPUT_CHARS = 80_000


def _flag(name: str, default: bool = False) -> bool:
    value = environment().get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def mcporter_command() -> str:
    configured = environment().get("OMNIX_AGENT_MCPORTER_COMMAND", "").strip()
    if configured:
        return configured
    repo_root = Path(__file__).resolve().parents[4]
    local_bin = repo_root / ".tools" / "npm-global"
    candidates = (
        local_bin / "mcporter.cmd",
        local_bin / "mcporter",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "mcporter"


def mcporter_available() -> bool:
    if not _flag("OMNIX_AGENT_MCP_ENABLED", True):
        return False
    command = mcporter_command()
    if os.path.isabs(command) or os.sep in command or (os.altsep and os.altsep in command):
        return Path(command).is_file()
    return shutil.which(command) is not None


def mcp_runtime_available() -> bool:
    from app.capabilities.mcp_policy import configured_mcp_capability_ids

    return mcporter_available() and bool(configured_mcp_capability_ids())


def _timeout_seconds() -> int:
    try:
        return max(5, min(int(environment().get("OMNIX_AGENT_MCP_TIMEOUT_SECONDS", "60")), 300))
    except ValueError:
        return 60


def _minimal_environment(server: McpServerPolicy) -> dict[str, str]:
    source = environment()
    env = {key: source[key] for key in _SAFE_ENV_KEYS if source.get(key)}
    allowed = set(server.env_keys) | set(server.headers_from_env.values())
    for key in allowed:
        if key and source.get(key) is not None:
            env[key] = source[key]
    return env


def _mcporter_server_config(server: McpServerPolicy) -> dict[str, Any]:
    if server.transport == "http":
        row: dict[str, Any] = {"url": server.url}
        if server.headers_from_env:
            row["headers"] = {
                header: f"$env:{env_key}"
                for header, env_key in server.headers_from_env.items()
            }
        return row
    row = {
        "command": server.command,
        "args": list(server.args),
    }
    if server.cwd:
        row["cwd"] = server.cwd
    if server.env_keys:
        row["env"] = {key: f"$env:{key}" for key in server.env_keys}
    return row


def _write_isolated_config(root: Path, server: McpServerPolicy) -> Path:
    config_path = root / "mcporter.json"
    payload = {"mcpServers": {server.name: _mcporter_server_config(server)}}
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path


def _run(argv: list[str], *, cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_timeout_seconds(),
    )


def _parse_output(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text[:_MAX_OUTPUT_CHARS]}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _validate_input(tool: McpToolPolicy, payload: dict[str, Any]) -> dict[str, Any]:
    # MCPorter performs schema validation against the live server.  Omnix still
    # bounds the envelope before handing it off so a tool call cannot smuggle
    # process/config flags or unbounded context through the generic input map.
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(serialized) > 64_000:
        raise ValueError("MCP input exceeds Omnix size limit")
    if not isinstance(payload, dict):
        raise ValueError("MCP tool input must be an object")
    return payload


def run_mcp_tool_request(request: AssistantToolRequest) -> AssistantToolResult:
    resolved = resolve_mcp_tool(request.action_id)
    if resolved is None:
        return AssistantToolResult(
            tool_id="mcp",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="MCP capability is not present in the operator policy.",
            error="mcp_capability_not_configured",
        )
    server, tool = resolved
    if not mcporter_available():
        return AssistantToolResult(
            tool_id="mcp",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="MCPorter is not installed or is disabled.",
            error="mcporter_runtime_unavailable",
        )
    try:
        payload = _validate_input(tool, dict(request.input))
    except (TypeError, ValueError) as exc:
        return AssistantToolResult(
            tool_id="mcp",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="MCP request was rejected by Omnix policy.",
            error=f"mcp_policy_rejected:{exc}",
        )

    try:
        with tempfile.TemporaryDirectory(prefix="omnix-mcporter-") as temp:
            root = Path(temp)
            config = _write_isolated_config(root, server)
            argv = [
                mcporter_command(),
                "--config",
                str(config),
                "--root",
                str(root),
                "call",
                f"{server.name}.{tool.name}",
                "--args",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
                "--output",
                "json",
                "--no-oauth",
                "--timeout",
                str(_timeout_seconds() * 1000),
            ]
            completed = _run(argv, cwd=str(root), env=_minimal_environment(server))
    except (OSError, subprocess.SubprocessError) as exc:
        return AssistantToolResult(
            tool_id="mcp",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="MCPorter failed to execute the governed MCP tool.",
            error=f"mcporter_runtime_error:{type(exc).__name__}",
        )

    stdout = (completed.stdout or "")[:_MAX_OUTPUT_CHARS]
    stderr = (completed.stderr or "")[:8_000]
    if completed.returncode != 0:
        return AssistantToolResult(
            tool_id="mcp",
            action_id=request.action_id,
            session_id=request.session_id,
            state_changed=False,
            result_summary=f"MCP tool failed with exit code {completed.returncode}.",
            output={"stdout": stdout, "stderr": stderr, "server": server.name, "tool": tool.name},
            error="mcp_tool_failed",
        )

    return AssistantToolResult(
        tool_id="mcp",
        action_id=request.action_id,
        session_id=request.session_id,
        state_changed=tool.effect in {"create", "mutate", "delete", "execute"},
        result_summary=f"Completed governed MCP tool {server.name}.{tool.name}.",
        output={
            "server": server.name,
            "tool": tool.name,
            "result": _parse_output(stdout),
        },
    )
