"""Operator-owned MCP policy compiled into Omnix capability authority.

MCP server/tool metadata is never authority by itself.  Only tools explicitly
listed in this policy are projected into the canonical capability registry.  The
runtime deliberately does not import Cursor/Claude/Codex MCP configuration via
MCPorter because doing so would create a parallel authority path outside the
RunSpec.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.caching.bounded_cache import bounded_lru_cache

McpTransport = Literal["http", "stdio"]
McpEffect = Literal["read", "create", "mutate", "delete", "execute"]
McpRisk = Literal["low", "medium", "high"]
McpApproval = Literal["allow_automatic", "ask_sensitive", "always_ask", "disabled"]

DEFAULT_MCP_POLICY_PATH = Path("resources/config/agent_mcp_policy.json")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAPABILITY = re.compile(
    r"^mcp\.[a-z][a-z0-9_]{0,63}\.[a-z][a-z0-9_]{0,63}$"
)


class McpToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # This is the exact MCP protocol tool name and may contain punctuation.  It
    # never becomes an Omnix identifier directly.
    name: str
    capability_id: str
    description: str = "Governed MCP tool."
    effect: McpEffect = "read"
    risk: McpRisk = "low"
    approval_policy: McpApproval = "allow_automatic"
    enabled: bool = True
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self) -> "McpToolPolicy":
        if not self.name.strip() or any(ch.isspace() for ch in self.name):
            raise ValueError("MCP tool names must be non-empty and contain no whitespace")
        if len(self.name) > 200:
            raise ValueError("MCP tool name is too long")
        if not _CAPABILITY.fullmatch(self.capability_id):
            raise ValueError(
                "MCP capability ids must use canonical mcp.<server>.<tool> identifiers"
            )
        return self


class McpServerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    transport: McpTransport
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env_keys: tuple[str, ...] = ()
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tools: tuple[McpToolPolicy, ...] = ()

    @model_validator(mode="after")
    def validate_transport(self) -> "McpServerPolicy":
        if not _NAME.fullmatch(self.name):
            raise ValueError("MCP server names must be lowercase canonical identifiers")
        if self.transport == "http":
            if not self.url or not self.url.startswith(("https://", "http://")):
                raise ValueError("HTTP MCP servers require an http(s) URL")
            parsed = urlparse(self.url)
            if not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP HTTP URL must have a hostname and no embedded credentials")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("cleartext MCP HTTP is limited to loopback")
            if self.command or self.args:
                raise ValueError("HTTP MCP servers cannot define a stdio command")
        else:
            if (
                not self.command
                or any(ch.isspace() for ch in self.command)
                or any(ch in self.command for ch in "\r\n")
            ):
                raise ValueError("stdio MCP servers require one executable token")
            if self.url:
                raise ValueError("stdio MCP servers cannot define a URL")
        prefix = f"mcp.{self.name}."
        for tool in self.tools:
            if not tool.capability_id.startswith(prefix):
                raise ValueError(
                    f"MCP capability {tool.capability_id} does not belong to server {self.name}"
                )
        return self


class McpPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    servers: tuple[McpServerPolicy, ...] = ()

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "McpPolicy":
        servers = [server.name for server in self.servers]
        if len(servers) != len(set(servers)):
            raise ValueError("duplicate MCP server name")
        capability_ids = [
            tool.capability_id
            for server in self.servers
            for tool in server.tools
        ]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("duplicate MCP capability id")
        return self


def mcp_policy_path() -> Path:
    configured = (_env_str("OMNIX_AGENT_MCP_POLICY_PATH", "") or "").strip()
    return Path(configured) if configured else DEFAULT_MCP_POLICY_PATH


def load_mcp_policy(path: Path | None = None) -> McpPolicy:
    """Load policy fail-closed.

    Invalid or unreadable operator policy exposes zero MCP authority rather than
    falling back to MCPorter's user/project configuration discovery.
    """

    signature = mcp_policy_signature(path)
    if signature is None:
        return McpPolicy()
    return _parsed_mcp_policy(*signature)


def mcp_policy_signature(path: Path | None = None) -> tuple[str, int, int] | None:
    """(path, modification time, size) of the policy file, or None when absent."""
    target = path or mcp_policy_path()
    try:
        stat = target.stat()
    except OSError:
        return None
    return str(target), stat.st_mtime_ns, stat.st_size


@bounded_lru_cache(max_entries=8, ttl_seconds=3600.0)
def _parsed_mcp_policy(path: str, _mtime_ns: int, _size: int) -> McpPolicy:
    """Parsed once per file version (WP-7.4): an edit changes the signature."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return McpPolicy.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        return McpPolicy()


def enabled_mcp_servers(path: Path | None = None) -> tuple[McpServerPolicy, ...]:
    return tuple(server for server in load_mcp_policy(path).servers if server.enabled)


def enabled_mcp_tools(path: Path | None = None) -> tuple[tuple[McpServerPolicy, McpToolPolicy], ...]:
    rows: list[tuple[McpServerPolicy, McpToolPolicy]] = []
    for server in enabled_mcp_servers(path):
        rows.extend((server, tool) for tool in server.tools if tool.enabled)
    return tuple(rows)


def configured_mcp_capability_ids(path: Path | None = None) -> tuple[str, ...]:
    return tuple(tool.capability_id for _server, tool in enabled_mcp_tools(path))


def resolve_mcp_tool(capability_id: str, path: Path | None = None) -> tuple[McpServerPolicy, McpToolPolicy] | None:
    canonical = str(capability_id or "").strip()
    return next(
        (
            (server, tool)
            for server, tool in enabled_mcp_tools(path)
            if tool.capability_id == canonical
        ),
        None,
    )


def infer_mcp_capabilities_for_task(task: str, path: Path | None = None) -> tuple[str, ...]:
    """Infer only operator-configured MCP capabilities from explicit task text.

    A generic MCP/MCPorter request grants the configured set; otherwise a tool
    is inferred only when the prompt names its server, MCP tool name, or exact
    capability id.  This helper never discovers new servers/tools.
    """

    text = str(task or "")
    folded = text.casefold()
    rows = enabled_mcp_tools(path)
    if not rows:
        return ()
    matched: list[str] = []
    for server, tool in rows:
        candidates = (
            server.name.casefold(),
            tool.name.casefold(),
            tool.capability_id.casefold(),
        )
        if any(candidate and candidate in folded for candidate in candidates):
            matched.append(tool.capability_id)
    if matched:
        return tuple(dict.fromkeys(matched))

    # A generic "use MCP" request is only unambiguous when exactly one server
    # is configured. With multiple configured servers, require the prompt to
    # name a server/tool/capability rather than issuing the union of authority.
    generic_use = re.search(
        r"(?:\b(?:use|via|through|with|call|invoke|query|access)\b.{0,80}"
        r"\b(?:mcp|mcporter|model\s+context\s+protocol)\b|"
        r"\b(?:mcp|mcporter|model\s+context\s+protocol)\b.{0,80}"
        r"\b(?:use|call|invoke|query|access)\b)",
        text,
        re.I,
    )
    server_names = {server.name for server, _tool in rows}
    if generic_use and len(server_names) == 1:
        only_server = next(iter(server_names))
        return tuple(
            tool.capability_id
            for server, tool in rows
            if server.name == only_server
        )
    return ()
