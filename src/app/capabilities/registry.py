"""Canonical Omnix capability registry.

Capabilities describe authority independently from any one planner/runtime. Hermes,
Pi, the browser UI, and policy code consume projections of this catalog instead
of maintaining runtime-specific tool names.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.caching.bounded_cache import bounded_lru_cache
from app.runtime.ports import Port, implementations, installed_port_bindings

CapabilityExecutionZone = Literal["worker", "broker", "model", "context"]
CapabilityEffect = Literal["read", "create", "mutate", "delete", "execute"]
CapabilityRisk = Literal["low", "medium", "high"]
CapabilityApproval = Literal["allow_automatic", "ask_sensitive", "always_ask", "disabled"]


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    description: str
    namespace: str
    execution_zone: CapabilityExecutionZone
    effect: CapabilityEffect
    risk: CapabilityRisk = "low"
    scope_type: str = "workspace"
    approval_policy: CapabilityApproval = "allow_automatic"
    network_required: bool = False
    credential_required: bool = False
    audited: bool = True
    enabled: bool = True
    requires_connection: bool = False
    requires_confirmation: bool = False
    destructive: bool = False
    provider: str | None = None
    category: str = "general"
    assistant_visible: bool = False
    hermes_visible: bool = False
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    aliases: tuple[str, ...] = ()


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[Capability]) -> None:
        rows = list(capabilities)
        ids = [row.id for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate capability id")
        self._by_id = {row.id: row for row in rows}
        self._aliases: dict[str, str] = {}
        for row in rows:
            for alias in row.aliases:
                if alias in self._aliases or alias in self._by_id:
                    raise ValueError(f"duplicate capability alias: {alias}")
                self._aliases[alias] = row.id

    def all(self) -> list[Capability]:
        return list(self._by_id.values())

    def get(self, capability_id: str) -> Capability | None:
        canonical = self._aliases.get(capability_id, capability_id)
        return self._by_id.get(canonical)

    def canonical_id(self, capability_id: str) -> str | None:
        row = self.get(capability_id)
        return row.id if row is not None else None

    def for_namespace(self, namespace: str) -> list[Capability]:
        return [row for row in self._by_id.values() if row.namespace == namespace]

    def assistant_projection(self) -> list[Capability]:
        return [row for row in self._by_id.values() if row.assistant_visible]

    def hermes_projection(self) -> list[Capability]:
        return [row for row in self._by_id.values() if row.hermes_visible]


def _cap(
    capability_id: str,
    name: str,
    description: str,
    *,
    zone: CapabilityExecutionZone,
    effect: CapabilityEffect,
    risk: CapabilityRisk = "low",
    namespace: str | None = None,
    scope_type: str = "workspace",
    approval: CapabilityApproval | None = None,
    network: bool = False,
    credentials: bool = False,
    enabled: bool = True,
    connection: bool = False,
    confirmation: bool = False,
    destructive: bool = False,
    provider: str | None = None,
    category: str = "general",
    assistant: bool = False,
    hermes: bool = False,
    input_schema: dict[str, Any] | None = None,
    aliases: tuple[str, ...] = (),
) -> Capability:
    ns = namespace or capability_id.split(".", 1)[0]
    if approval is None:
        if destructive or confirmation or risk == "high":
            approval = "always_ask"
        elif effect in {"create", "mutate", "delete", "execute"} or risk == "medium":
            approval = "ask_sensitive"
        else:
            approval = "allow_automatic"
    return Capability(
        id=capability_id,
        name=name,
        description=description,
        namespace=ns,
        execution_zone=zone,
        effect=effect,
        risk=risk,
        scope_type=scope_type,
        approval_policy=approval,
        network_required=network,
        credential_required=credentials,
        enabled=enabled,
        requires_connection=connection,
        requires_confirmation=confirmation,
        destructive=destructive,
        provider=provider,
        category=category,
        assistant_visible=assistant,
        hermes_visible=hermes,
        input_schema=input_schema or {},
        aliases=aliases,
    )


_BROWSER_CAPABILITIES = (
    _cap("browser.open", "Open browser page", "Open an allowed browser origin in an isolated agent-browser session.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"url": "http(s) URL on the Omnix browser allowlist"}),
    _cap("browser.snapshot", "Snapshot browser page", "Read the current page accessibility snapshot with stable element references.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True),
    _cap("browser.click", "Click browser element", "Click one selector/reference in the governed browser session.", zone="broker", effect="execute", risk="medium", approval="allow_automatic", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref"}),
    _cap("browser.fill", "Fill browser field", "Fill one field in the governed browser session.", zone="broker", effect="execute", risk="medium", approval="allow_automatic", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "text": "bounded text"}),
    _cap("browser.press", "Press browser key", "Press one bounded key chord in the governed browser session.", zone="broker", effect="execute", risk="medium", approval="allow_automatic", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"key": "Enter, Tab, Control+a, etc."}),
    _cap("browser.hover", "Hover browser element", "Hover one selector/reference in the governed browser session.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref"}),
    _cap("browser.select", "Select browser option", "Select one option in the governed browser session.", zone="broker", effect="execute", risk="medium", approval="allow_automatic", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "value": "option value"}),
    _cap("browser.scroll", "Scroll browser page", "Scroll the governed browser page without arbitrary JavaScript.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"direction": "up/down/left/right", "pixels": "optional integer"}),
    _cap("browser.wait", "Wait for browser state", "Wait for a bounded selector, text, URL, load-state, or duration condition.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True),
    _cap("browser.get_text", "Read browser text", "Read text from one selector/reference in the current page.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref"}),
    _cap("browser.get_attribute", "Read browser attribute", "Read one DOM attribute without arbitrary JavaScript.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "attribute": "attribute name"}),
    _cap("browser.get_url", "Read browser URL", "Read the active governed browser URL.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True),
    _cap("browser.screenshot", "Capture browser screenshot", "Capture screenshot evidence from the governed browser session.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"full_page": "optional boolean"}),
    _cap("browser.assert_text_contains", "Assert browser text", "Deterministically require an element's text to contain an expected value.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "expected": "required text substring"}),
    _cap("browser.assert_text_not_contains", "Assert browser text absent", "Deterministically require an element's text not to contain a forbidden value.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "expected": "forbidden text substring"}),
    _cap("browser.assert_attribute_contains", "Assert browser attribute", "Deterministically require one DOM attribute to contain an expected value.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"selector": "agent-browser selector or @ref", "attribute": "attribute name", "expected": "required substring"}),
    _cap("browser.assert_url_contains", "Assert browser URL", "Deterministically require the active browser URL to contain an expected value.", zone="broker", effect="read", network=True, connection=True, provider="agent-browser", category="development", assistant=True, input_schema={"expected": "required URL substring"}),
    _cap("browser.close", "Close browser session", "Close the isolated governed browser session.", zone="broker", effect="execute", approval="allow_automatic", network=True, connection=True, provider="agent-browser", category="development", assistant=True),
)


_DEFAULT_CAPABILITIES = (
    _cap("workspace.read", "Read workspace file", "Read a UTF-8 file within the issued workspace scope.", zone="worker", effect="read", category="development", input_schema={"path": "workspace-relative path"}),
    _cap("workspace.list", "List workspace", "List entries within the issued workspace scope.", zone="worker", effect="read", category="development", input_schema={"path": "workspace-relative directory"}),
    _cap("workspace.search", "Search workspace", "Search text within the issued workspace scope.", zone="worker", effect="read", category="development", input_schema={"query": "string", "path": "workspace-relative path"}),
    _cap("workspace.edit", "Edit workspace file", "Apply a bounded text replacement inside the issued workspace.", zone="worker", effect="mutate", risk="medium", approval="allow_automatic", category="development", input_schema={"path": "workspace-relative path"}),
    _cap("workspace.write", "Write workspace file", "Write a file inside the issued workspace.", zone="worker", effect="mutate", risk="medium", approval="allow_automatic", category="development", input_schema={"path": "workspace-relative path"}),
    _cap("workspace.command", "Run workspace command", "Run an argv-only command allowed by the workspace command policy.", zone="worker", effect="execute", risk="high", approval="allow_automatic", category="development", input_schema={"argv": "list of strings"}),
    _cap("workspace.test", "Run workspace tests", "Run a configured test command in the issued workspace.", zone="worker", effect="execute", risk="medium", approval="allow_automatic", category="development", input_schema={"argv": "list of strings"}),
    _cap("workspace.git_status", "Read local git status", "Read git status for the isolated worktree.", zone="worker", effect="read", category="development"),
    _cap("workspace.git_diff", "Read local git diff", "Read the current isolated worktree diff as non-authoritative inspection context.", zone="worker", effect="read", category="development"),
    _cap("workspace.run_change_set", "Read authoritative run change set", "Read the canonical baseline-relative run-owned change subject bound to the exact candidate WorkspaceState.", zone="worker", effect="read", category="development"),
    _cap("gmail.read_email", "Read email", "Search and read Gmail messages and threads.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="Google", category="communication", assistant=True),
    _cap("gmail.create_draft", "Create drafts", "Create reviewable Gmail drafts without sending.", zone="broker", effect="create", risk="medium", network=True, credentials=True, connection=True, provider="Google", category="communication", assistant=True),
    _cap("gmail.send_email", "Send email", "Send or reply to Gmail messages.", zone="broker", effect="mutate", risk="high", network=True, credentials=True, connection=True, confirmation=True, provider="Google", category="communication", assistant=True),
    _cap("gmail.delete_email", "Delete email", "Move Gmail messages to Trash.", zone="broker", effect="delete", risk="high", network=True, credentials=True, enabled=False, connection=True, confirmation=True, destructive=True, provider="Google", category="communication", assistant=True),
    _cap("calendar.read_availability", "Read availability", "Read calendar events in an exact ISO-8601 time range.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="Google", category="productivity", assistant=True, hermes=True, input_schema={"start_time": "ISO 8601 datetime", "end_time": "ISO 8601 datetime", "timezone": "IANA timezone"}),
    _cap("calendar.create_event", "Create events", "Create a calendar event with explicit time and timezone.", zone="broker", effect="create", risk="medium", network=True, credentials=True, connection=True, confirmation=True, provider="Google", category="productivity", assistant=True, hermes=True, input_schema={"title": "string", "start_time": "ISO 8601 datetime", "end_time": "ISO 8601 datetime", "timezone": "IANA timezone", "attendees": "list of email addresses", "location": "string", "description": "string", "reminder_minutes": "integer 0..40320"}),
    _cap("calendar.delete_event", "Delete events", "Delete calendar events.", zone="broker", effect="delete", risk="high", network=True, credentials=True, connection=True, confirmation=True, destructive=True, provider="Google", category="productivity", assistant=True),
    _cap("contacts.search_contacts", "Search contacts", "Search Google Contacts.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="Google", category="productivity", assistant=True),
    _cap("contacts.resolve_recipient", "Resolve recipients", "Resolve saved contact details for another governed action.", zone="broker", effect="read", risk="medium", network=True, credentials=True, connection=True, provider="Google", category="productivity", assistant=True),
    _cap("weather.current", "Read current weather", "Read authoritative current weather for an explicitly resolved location when a weather provider is configured.", zone="broker", effect="read", network=True, connection=True, enabled=False, provider="Weather", category="research", assistant=True, hermes=True, input_schema={"location": "canonical location"}),
    _cap("github.read_repo", "Read repositories", "Read repository metadata, files, pull requests, and checks.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="GitHub", category="development", assistant=True),
    _cap("github.create_branch", "Create branches", "Create GitHub branches for prepared changes.", zone="broker", effect="create", risk="medium", network=True, credentials=True, connection=True, provider="GitHub", category="development", assistant=True),
    _cap("github.create_pr", "Open pull requests", "Open pull requests from prepared branch changes.", zone="broker", effect="create", risk="medium", network=True, credentials=True, connection=True, provider="GitHub", category="development", assistant=True),
    _cap("github.inspect_ci", "Inspect CI", "Read workflow/check status for a commit or pull request.", zone="broker", effect="read", network=True, credentials=True, connection=True, provider="GitHub", category="development", assistant=True),
    _cap("github.push", "Push prepared branch", "Publish an isolated local branch to the configured GitHub remote.", zone="broker", effect="mutate", risk="medium", network=True, credentials=True, connection=True, provider="GitHub", category="development", assistant=True),
    _cap("github.merge_pr", "Merge pull requests", "Merge pull requests after required checks pass.", zone="broker", effect="mutate", risk="high", network=True, credentials=True, connection=True, confirmation=True, provider="GitHub", category="development", assistant=True),
    *_BROWSER_CAPABILITIES,
    _cap("home.list_devices", "List home devices", "List devices exposed by the semantic Omnix Home adapter.", zone="broker", effect="read", network=True, connection=True, provider="Omnix Home", category="smart-home", assistant=True, hermes=True),
    _cap("home.get_state", "Read home device state", "Read verified semantic state for a selected home device.", zone="broker", effect="read", network=True, connection=True, provider="Omnix Home", category="smart-home", assistant=True, hermes=True, input_schema={"target": "semantic device/room name"}),
    _cap("home.set_state", "Set home device state", "Set and verify semantic state for a selected home device.", zone="broker", effect="mutate", risk="medium", network=True, connection=True, confirmation=True, approval="always_ask", provider="Omnix Home", category="smart-home", assistant=True, hermes=True, input_schema={"target": "semantic device/room name", "on": "boolean"}),
    _cap("home.get_energy", "Read home energy", "Read available energy telemetry without mutating devices.", zone="broker", effect="read", network=True, connection=True, provider="Omnix Home", category="smart-home", assistant=True, hermes=True, input_schema={"target": "optional device/room"}),
    _cap("home.apply_scene", "Apply home scene", "Apply and verify a bounded semantic home scene.", zone="broker", effect="mutate", risk="high", network=True, connection=True, confirmation=True, approval="always_ask", provider="Omnix Home", category="smart-home", assistant=True, hermes=True, input_schema={"actions": "list of {target,on}"}),
    _cap("kasa.discover_devices", "Discover Kasa devices", "Discover supported TP-Link Kasa devices.", zone="broker", effect="read", network=True, connection=True, provider="TP-Link", category="smart-home", assistant=True, hermes=True, aliases=("kasa_discover_devices",)),
    _cap("kasa.get_state", "Read plug state", "Read the verified state of a selected Kasa device.", zone="broker", effect="read", network=True, connection=True, provider="TP-Link", category="smart-home", assistant=True, hermes=True, input_schema={"target": "string alias, host, or device id; optional when exactly one device exists"}, aliases=("kasa_get_state",)),
    _cap("kasa.turn_on", "Turn on plug", "Turn on one selected Kasa plug and verify its state.", zone="broker", effect="mutate", risk="medium", network=True, connection=True, confirmation=True, approval="always_ask", provider="TP-Link", category="smart-home", assistant=True, hermes=True, input_schema={"target": "string alias, host, or device id"}, aliases=("kasa_turn_on",)),
    _cap("kasa.turn_off", "Turn off plug", "Turn off one selected Kasa plug and verify its state.", zone="broker", effect="mutate", risk="medium", network=True, connection=True, confirmation=True, approval="always_ask", provider="TP-Link", category="smart-home", assistant=True, hermes=True, input_schema={"target": "string alias, host, or device id"}, aliases=("kasa_turn_off",)),
    _cap("house.get_status", "Read house status", "Read the current Omnix house status.", zone="context", effect="read", category="smart-home", hermes=True, aliases=("get_house_status",)),
    _cap("hermes.get_status", "Read Hermes status", "Read Hermes runtime status.", zone="context", effect="read", category="platform", hermes=True, aliases=("get_hermes_status",)),
    _cap("hermes.get_diagnostics_schema", "Read Hermes diagnostics schema", "Read Hermes diagnostics schema.", zone="context", effect="read", category="platform", hermes=True, aliases=("get_hermes_diagnostics_schema",)),
)


def _configured_mcp_capabilities() -> tuple[Capability, ...]:
    """Project explicit operator MCP policy into canonical capabilities."""

    try:
        from .mcp_policy import enabled_mcp_tools

        rows = enabled_mcp_tools()
    except Exception:
        return ()
    capabilities: list[Capability] = []
    for server, tool in rows:
        capabilities.append(
            _cap(
                tool.capability_id,
                f"MCP · {server.name} · {tool.name}",
                tool.description,
                zone="broker",
                effect=tool.effect,
                risk=tool.risk,
                namespace="mcp",
                approval=tool.approval_policy,
                network=True,
                credentials=bool(server.env_keys or server.headers_from_env),
                connection=True,
                provider="MCPorter",
                category="development",
                assistant=True,
                input_schema=dict(tool.input_schema),
            )
        )
    return tuple(capabilities)


def browser_capability_ids() -> tuple[str, ...]:
    return tuple(capability.id for capability in _BROWSER_CAPABILITIES)


class ToolDeclaration(Protocol):
    """A module's assistant tool (ADR-0016): its catalog rows, display text,
    default enablement and adapter. Declaring a tool makes it available; it
    grants nothing. Grants, approvals and the tool policy still decide."""

    tool_id: str
    display_name: str
    description: str
    account_label: str | None

    def capabilities(self) -> tuple[Capability, ...]: ...

    def default_enabled(self) -> bool: ...

    def run(self, request: Any) -> Any: ...


TOOL_DECLARATIONS: Port[ToolDeclaration] = Port("capabilities.tools", ToolDeclaration, "many")


def capability(capability_id: str, name: str, description: str, **options: Any) -> Capability:
    """Build a catalog row with the kernel's defaults (approval policy by effect and risk)."""
    return _cap(capability_id, name, description, **options)


def declared_tools() -> tuple[ToolDeclaration, ...]:
    return implementations(TOOL_DECLARATIONS)


def default_capability_registry() -> CapabilityRegistry:
    """The built-in capabilities, the tools enabled features declare and the
    operator's MCP tools; rebuilt when the MCP policy file or the composed
    bindings change (WP-7.4)."""
    from .mcp_policy import mcp_policy_signature

    return _capability_registry_for(mcp_policy_signature(), installed_port_bindings())


# Fields that only describe a capability to people. Every other field bounds
# what an approval authorizes, so it is in the definition hash; a field added
# later is covered automatically unless it is listed here (with review).
DISPLAY_ONLY_CAPABILITY_FIELDS = frozenset({
    "name", "description", "category", "aliases", "assistant_visible", "hermes_visible",
})


def _definition_hash(row: Capability) -> str:
    definition = row.model_dump(mode="json", exclude=set(DISPLAY_ONLY_CAPABILITY_FIELDS))
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capability_definition_hash(capability_id: str) -> str | None:
    """The hash of a capability's authority-relevant definition in this process's
    catalog, or ``None`` when the catalog does not offer it. An approval records
    it when issued; execution refuses a missing or different hash (PA-1.4)."""
    row = default_capability_registry().get(capability_id)
    return _definition_hash(row) if row is not None else None


def capability_catalog_digest() -> str:
    """A digest of the whole catalog, for diagnostics only. Processes may run
    different catalogs during rolling upgrades and MCP policy reloads, so a
    mismatch never refuses an execution; approvals are bound per capability."""
    rows = sorted(default_capability_registry().all(), key=lambda row: row.id)
    canonical = json.dumps([[row.id, _definition_hash(row)] for row in rows], separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@bounded_lru_cache(max_entries=8, ttl_seconds=3600.0)
def _capability_registry_for(_policy_signature: tuple[str, int, int] | None, _bindings: object = None) -> CapabilityRegistry:
    contributed = tuple(row for tool in declared_tools() for row in tool.capabilities())
    return CapabilityRegistry((*_DEFAULT_CAPABILITIES, *contributed, *_configured_mcp_capabilities()))
