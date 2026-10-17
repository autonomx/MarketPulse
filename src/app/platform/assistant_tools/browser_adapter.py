"""Governed browser adapter backed by Vercel Labs agent-browser.

Pi never receives the raw ``agent-browser`` executable. Browser operations are
canonical Omnix capabilities dispatched here after RunSpec, approval and budget
checks. The adapter adds agent-browser's own domain/network containment as a
second boundary and disables page-provided WebMCP tools.

For coding UI validation, ``browser.open`` also supports an Omnix-owned
``workspace_preview`` mode. The backend resolves the exact run worktree, starts
the repository web app on an allocated loopback port without shell composition,
and tears the preview/browser session down automatically after a passing
deterministic assertion. The model never owns the preview process lifecycle.

On Windows, the adapter can use its bounded Playwright worker when the native
agent-browser daemon cannot establish a CDP channel. That fallback preserves
run-scoped sessions and Omnix's navigation/resource allowlist.
"""
from __future__ import annotations

import logging
from app.config.env import env_str as _env_str, environment as _environment

import atexit
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from queue import Full, Queue
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .models import AssistantToolRequest, AssistantToolResult
from app.runtime.process_environment import bounded_process_environment

logger = logging.getLogger(__name__)

_BROWSER_ACTIONS = {
    "browser.open",
    "browser.snapshot",
    "browser.click",
    "browser.fill",
    "browser.press",
    "browser.hover",
    "browser.select",
    "browser.scroll",
    "browser.wait",
    "browser.get_text",
    "browser.get_attribute",
    "browser.get_url",
    "browser.screenshot",
    "browser.assert_text_contains",
    "browser.assert_text_not_contains",
    "browser.assert_attribute_contains",
    "browser.assert_url_contains",
    "browser.close",
}
_ASSERTIONS = {
    "browser.assert_text_contains",
    "browser.assert_text_not_contains",
    "browser.assert_attribute_contains",
    "browser.assert_url_contains",
}
_INTERACTIVE = {
    "browser.click",
    "browser.fill",
    "browser.press",
    "browser.select",
}
_DEFAULT_ALLOWED_DOMAINS = ("localhost", "127.0.0.1", "::1")
_SAFE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
)
_SELECTOR = re.compile(r"^.{1,1024}$", re.S)
_MAX_VALUE_CHARS = 16_000
_MAX_OUTPUT_CHARS = 50_000
_MAX_BROWSER_SESSION_GENERATIONS = 4096
_BROWSER_SESSION_GENERATION_TTL_SECONDS = 3600.0
_MAX_PLAYWRIGHT_SESSIONS = 64
_PLAYWRIGHT_SESSION_TTL_SECONDS = 1800.0
_MAX_PLAYWRIGHT_COMMANDS_PER_SESSION = 64
_MAX_PREVIEW_ENTRIES = 64
_MAX_PREVIEW_TTL_SECONDS = 1800.0
_DEFAULT_PREVIEW_PACKAGE_PATH = "web"


def _preview_package_path() -> str:
    """The web package a workspace preview runs (OMNIX_AGENT_PREVIEW_PACKAGE_PATH)."""
    value = (_env_str("OMNIX_AGENT_PREVIEW_PACKAGE_PATH", _DEFAULT_PREVIEW_PACKAGE_PATH) or "").strip().replace("\\", "/")
    parts = value.split("/")
    if not value or value.startswith("/") or ":" in value or ".." in parts:
        raise ValueError("OMNIX_AGENT_PREVIEW_PACKAGE_PATH must be a relative path inside the workspace")
    return value
_PREVIEW_ROUTE = re.compile(r"^/[^\r\n\x00]{0,2047}$", re.S)
# Some Windows hosts reject Chrome's out-of-process GPU child with
# STATUS_ACCESS_DENIED (0xC0000022), which closes the CDP channel before
# agent-browser can create a page. Keep this fixed and narrow: arbitrary
# browser startup args must not cross the governed adapter boundary.
_WINDOWS_CHROME_ARGS = "--in-process-gpu,--disable-gpu"


@dataclass
class _PlaywrightCommand:
    request: AssistantToolRequest
    argv: list[str]
    metadata: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    completed: subprocess.CompletedProcess[str] | None = None


@dataclass
class _PlaywrightSession:
    name: str
    commands: Queue[_PlaywrightCommand] = field(
        default_factory=lambda: Queue(maxsize=_MAX_PLAYWRIGHT_COMMANDS_PER_SESSION)
    )
    ready: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    startup_error: BaseException | None = None
    thread: threading.Thread | None = None
    idle_timer: threading.Timer | None = None
    expires_at: float = 0.0


@dataclass
class _WorkspacePreview:
    process: subprocess.Popen[str]
    url: str
    port: int
    timer: threading.Timer | None = None
    expires_at: float = 0.0
    # Sandboxed previews remove their containers here (WP-4.7).
    cleanup: Callable[[], None] | None = None


_PREVIEW_LOCK = threading.RLock()
_PREVIEWS: OrderedDict[str, _WorkspacePreview] = OrderedDict()
_BROWSER_SESSION_LOCK = threading.RLock()
_BROWSER_SESSION_GENERATIONS: OrderedDict[str, tuple[int, float]] = OrderedDict()
_PLAYWRIGHT_LOCK = threading.RLock()
_PLAYWRIGHT_SESSIONS: dict[str, _PlaywrightSession] = {}


def _flag(name: str, default: bool = False) -> bool:
    value = _env_str(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def agent_browser_command() -> str:
    configured = _env_str("OMNIX_AGENT_BROWSER_COMMAND", "").strip()
    if configured:
        return configured
    repo_root = Path(__file__).resolve().parents[4]
    local_bin = repo_root / ".tools" / "npm-global"
    candidates = (
        local_bin / "agent-browser.cmd",
        local_bin / "agent-browser",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "agent-browser"


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _browser_backend() -> str:
    configured = _env_str("OMNIX_AGENT_BROWSER_BACKEND", "").strip().casefold()
    if configured in {"agent-browser", "playwright"}:
        return configured
    # The native agent-browser Windows daemon currently loses its CDP channel
    # on this host. Playwright is already a declared Omnix dependency and uses
    # the same Chrome executable successfully with the compatibility flags.
    if os.name == "nt" and _playwright_available():
        return "playwright"
    return "agent-browser"


def _playwright_executable() -> str | None:
    configured = _env_str("AGENT_BROWSER_EXECUTABLE_PATH", "").strip()
    if configured and Path(configured).is_file():
        return configured
    roots = [
        _env_str("PROGRAMFILES", ""),
        _env_str("PROGRAMFILES(X86)", ""),
        _env_str("LOCALAPPDATA", ""),
    ]
    relative = Path("Google") / "Chrome" / "Application" / "chrome.exe"
    for root in roots:
        if root:
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def browser_available() -> bool:
    if not _flag("OMNIX_AGENT_BROWSER_ENABLED", True):
        return False
    if _browser_backend() == "playwright":
        return _playwright_available() and _playwright_executable() is not None
    command = agent_browser_command()
    if os.path.isabs(command) or os.sep in command or (os.altsep and os.altsep in command):
        return Path(command).is_file()
    return shutil.which(command) is not None


def browser_allowed_domains() -> tuple[str, ...]:
    raw = _env_str("OMNIX_AGENT_BROWSER_ALLOWED_DOMAINS", "").strip()
    if not raw:
        return _DEFAULT_ALLOWED_DOMAINS
    values: list[str]
    try:
        parsed = json.loads(raw)
        values = [str(item) for item in parsed] if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        values = raw.split(",")
    normalized = []
    for item in values:
        value = item.strip().casefold()
        if value and not any(ch.isspace() for ch in value):
            normalized.append(value)
    return tuple(dict.fromkeys(normalized)) or _DEFAULT_ALLOWED_DOMAINS


def _domain_allowed(hostname: str) -> bool:
    host = hostname.strip().casefold().rstrip(".")
    if not host:
        return False
    for pattern in browser_allowed_domains():
        candidate = pattern.strip().casefold().rstrip(".")
        if candidate.startswith("*."):
            suffix = candidate[2:]
            if host == suffix or host.endswith("." + suffix):
                return True
        elif host == candidate:
            return True
    return False


def _validate_open_url(value: object) -> str:
    url = str(value or "").strip()
    if url == "about:blank":
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser.open requires an http(s) URL or about:blank")
    if parsed.username or parsed.password:
        raise ValueError("browser.open does not allow credentials embedded in URLs")
    if not _domain_allowed(parsed.hostname):
        raise ValueError("browser origin is outside OMNIX_AGENT_BROWSER_ALLOWED_DOMAINS")
    return url


def _run_id_from_proposal(proposal_id: object) -> str | None:
    proposal = str(proposal_id or "").strip()
    if not proposal.startswith("agent:"):
        return None
    parts = proposal.split(":", 2)
    if len(parts) != 3 or not parts[1].strip():
        return None
    return parts[1].strip()


def _browser_session_key(session_id: str | None, proposal_id: object = None) -> str:
    return _run_id_from_proposal(proposal_id) or str(session_id or "omnix")


def _session_name(session_id: str | None, *, proposal_id: object = None) -> str:
    key = _browser_session_key(session_id, proposal_id)
    with _BROWSER_SESSION_LOCK:
        generation = _browser_session_generation_locked(key, time.monotonic())
    identity = f"{key}:{generation}" if generation else key
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"omnix-{digest}"


def _browser_session_generation_locked(key: str, now: float) -> int:
    expired = [
        name
        for name, (_generation, touched) in _BROWSER_SESSION_GENERATIONS.items()
        if now - touched > _BROWSER_SESSION_GENERATION_TTL_SECONDS
    ]
    for name in expired:
        _BROWSER_SESSION_GENERATIONS.pop(name, None)
    entry = _BROWSER_SESSION_GENERATIONS.get(key)
    if entry is None:
        return 0
    _BROWSER_SESSION_GENERATIONS.move_to_end(key)
    generation, _touched = entry
    return generation


def _safe_text(value: object, *, field: str, max_chars: int = _MAX_VALUE_CHARS) -> str:
    text = str(value or "")
    if not text or len(text) > max_chars or "\x00" in text:
        raise ValueError(f"browser {field} is missing or too large")
    return text


def _safe_selector(value: object) -> str:
    selector = str(value or "").strip()
    if not selector or not _SELECTOR.fullmatch(selector):
        raise ValueError("browser selector is missing or too large")
    return selector


def _timeout_seconds() -> int:
    try:
        return max(5, min(int(_env_str("OMNIX_AGENT_BROWSER_TIMEOUT_SECONDS", "45")), 180))
    except ValueError:
        return 45


def _preview_start_timeout_seconds() -> int:
    try:
        return max(3, min(int(_env_str("OMNIX_AGENT_PREVIEW_START_TIMEOUT_SECONDS", "20")), 60))
    except ValueError:
        return 20


def _preview_ttl_seconds() -> int:
    try:
        return max(
            30,
            min(
                int(_env_str("OMNIX_AGENT_PREVIEW_TTL_SECONDS", "300")),
                int(_MAX_PREVIEW_TTL_SECONDS),
            ),
        )
    except ValueError:
        return 300


def _minimal_environment() -> dict[str, str]:
    source = _environment()
    env = bounded_process_environment(source, _SAFE_ENV_KEYS)
    executable = _env_str("AGENT_BROWSER_EXECUTABLE_PATH", "").strip()
    if executable:
        env["AGENT_BROWSER_EXECUTABLE_PATH"] = executable
    if os.name == "nt":
        env["AGENT_BROWSER_ARGS"] = _WINDOWS_CHROME_ARGS
    return env


def _preview_environment() -> dict[str, str]:
    env = _minimal_environment()
    for key, value in _environment().items():
        # VITE_* values are deliberately browser-public configuration. Do not
        # leak arbitrary Omnix/backend environment variables into repository
        # code executed by the preview process.
        if key.startswith("VITE_"):
            env[key] = value
    return env


def _base_argv(request: AssistantToolRequest) -> list[str]:
    return [
        agent_browser_command(),
        "--session",
        _session_name(request.session_id, proposal_id=request.proposal_id),
        "--allowed-domains",
        ",".join(browser_allowed_domains()),
        "--content-boundaries",
        "--max-output",
        str(_MAX_OUTPUT_CHARS),
        "--no-webmcp",
    ]


def _playwright_route(route: Any) -> None:
    parsed = urlparse(route.request.url)
    if parsed.scheme in {"http", "https"} and parsed.hostname and not _domain_allowed(parsed.hostname):
        route.abort()
        return
    route.continue_()


def _playwright_locator(page: Any, selector: str) -> Any:
    if selector.startswith("@"):
        match = re.fullmatch(r"@e([1-9][0-9]*)", selector)
        if not match:
            raise ValueError("Playwright browser fallback only accepts @eN element references")
        index = int(match.group(1)) - 1
        return page.locator("button, a, input, textarea, select, [role], [tabindex]").nth(index)
    return page.locator(selector).first


def _playwright_snapshot(page: Any, timeout_ms: int) -> str:
    body = page.locator("body")
    try:
        snapshot = body.aria_snapshot(timeout=timeout_ms)
    except AttributeError:
        snapshot = body.inner_text(timeout=timeout_ms)
    if not snapshot:
        snapshot = body.inner_text(timeout=timeout_ms)
    return json.dumps(
        {"data": {"snapshot": snapshot}, "success": True},
        ensure_ascii=False,
    )


def _log_playwright_worker_event(session: _PlaywrightSession, event: str) -> None:
    try:
        from app.observability.agent_logging import log_agent_activity

        log_agent_activity(
            event,
            category="browser",
            fields={"backend": "playwright", "session": session.name},
        )
    except Exception:
        logger.debug("suppressed error in %s", "_log_playwright_worker_event", exc_info=True)


def _playwright_action(
    page: Any,
    request: AssistantToolRequest,
    metadata: dict[str, Any],
    timeout_seconds: int,
) -> tuple[str, bool]:
    """Execute one bounded browser action on the dedicated Playwright thread."""

    action = request.action_id
    payload = dict(request.input)
    timeout_ms = max(1_000, timeout_seconds * 1_000)
    if action == "browser.open":
        page.goto(
            str(metadata["url"]),
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        return "", False
    if action == "browser.snapshot":
        return _playwright_snapshot(page, timeout_ms), False
    if action == "browser.click":
        _playwright_locator(page, _safe_selector(payload.get("selector"))).click(timeout=timeout_ms)
        return "", False
    if action == "browser.fill":
        _playwright_locator(page, _safe_selector(payload.get("selector"))).fill(
            _safe_text(payload.get("text"), field="text"),
            timeout=timeout_ms,
        )
        return "", False
    if action == "browser.press":
        page.keyboard.press(_safe_text(payload.get("key"), field="key", max_chars=128))
        return "", False
    if action == "browser.hover":
        _playwright_locator(page, _safe_selector(payload.get("selector"))).hover(timeout=timeout_ms)
        return "", False
    if action == "browser.select":
        _playwright_locator(page, _safe_selector(payload.get("selector"))).select_option(
            _safe_text(payload.get("value"), field="value", max_chars=2_048),
            timeout=timeout_ms,
        )
        return "", False
    if action == "browser.scroll":
        direction = str(payload.get("direction") or "down").strip().casefold()
        pixels = int(payload.get("pixels") or 500)
        delta_x = pixels if direction == "right" else -pixels if direction == "left" else 0
        delta_y = pixels if direction == "down" else -pixels if direction == "up" else 0
        page.mouse.wheel(delta_x, delta_y)
        return "", False
    if action == "browser.wait":
        supplied = [
            key for key in ("selector", "text", "url", "milliseconds", "load")
            if payload.get(key) not in {None, ""}
        ]
        if len(supplied) != 1:
            raise ValueError("browser.wait requires exactly one bounded wait condition")
        kind = supplied[0]
        if kind == "selector":
            page.locator(_safe_selector(payload[kind])).first.wait_for(
                state="attached", timeout=timeout_ms
            )
        elif kind == "text":
            page.get_by_text(_safe_text(payload[kind], field="text", max_chars=4_096)).first.wait_for(
                state="visible", timeout=timeout_ms
            )
        elif kind == "url":
            page.wait_for_url(
                _safe_text(payload[kind], field="url pattern", max_chars=2_048),
                timeout=timeout_ms,
            )
        elif kind == "load":
            state = str(payload[kind]).strip().casefold()
            if state not in {"load", "domcontentloaded", "networkidle"}:
                raise ValueError("browser.wait load state is not allowed")
            page.wait_for_load_state(state, timeout=timeout_ms)
        else:
            milliseconds = int(payload[kind])
            if milliseconds < 0 or milliseconds > 30_000:
                raise ValueError("browser.wait milliseconds outside allowed range")
            page.wait_for_timeout(milliseconds)
        return "", False
    if action == "browser.get_text":
        return _playwright_locator(page, _safe_selector(payload.get("selector"))).inner_text(
            timeout=timeout_ms
        ), False
    if action == "browser.get_attribute":
        value = _playwright_locator(page, _safe_selector(payload.get("selector"))).get_attribute(
            _safe_text(payload.get("attribute"), field="attribute", max_chars=256),
            timeout=timeout_ms,
        )
        return str(value or ""), False
    if action == "browser.get_url":
        return page.url, False
    if action == "browser.screenshot":
        page.screenshot(
            path=str(metadata["screenshot_path"]),
            full_page=bool(payload.get("full_page")),
            timeout=timeout_ms,
        )
        return "", False
    if action == "browser.assert_text_contains" or action == "browser.assert_text_not_contains":
        return _playwright_locator(page, _safe_selector(payload.get("selector"))).inner_text(
            timeout=timeout_ms
        ), False
    if action == "browser.assert_attribute_contains":
        value = _playwright_locator(page, _safe_selector(payload.get("selector"))).get_attribute(
            _safe_text(payload.get("attribute"), field="attribute", max_chars=256),
            timeout=timeout_ms,
        )
        return str(value or ""), False
    if action == "browser.assert_url_contains":
        return page.url, False
    if action == "browser.close":
        return "", True
    raise ValueError("unsupported browser capability")


def _playwright_worker(session: _PlaywrightSession) -> None:
    playwright = browser = context = page = None
    _log_playwright_worker_event(session, "browser.worker_started")
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        _log_playwright_worker_event(session, "browser.playwright_starting")
        playwright = sync_playwright().start()
        _log_playwright_worker_event(session, "browser.playwright_started")
        executable = _playwright_executable()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--in-process-gpu",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if executable:
            launch_kwargs["executable_path"] = executable
        _log_playwright_worker_event(session, "browser.chrome_starting")
        browser = playwright.chromium.launch(**launch_kwargs)
        _log_playwright_worker_event(session, "browser.chrome_started")
        context = browser.new_context(service_workers="block")
        context.route("**/*", _playwright_route)
        page = context.new_page()
    except Exception as exc:
        session.startup_error = exc
        session.ready.set()
        session.stopped.set()
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                logger.debug("suppressed error in %s", "_playwright_worker", exc_info=True)
        return

    session.ready.set()
    try:
        while True:
            command = session.commands.get()
            should_stop = False
            try:
                stdout, should_stop = _playwright_action(
                    page,
                    command.request,
                    command.metadata,
                    _timeout_seconds(),
                )
                command.completed = subprocess.CompletedProcess(
                    command.argv, 0, stdout=stdout, stderr=""
                )
            except PlaywrightTimeoutError as exc:
                command.completed = subprocess.CompletedProcess(
                    command.argv, 1, stdout="", stderr=f"TimeoutError: {exc}"
                )
            except BaseException as exc:
                command.completed = subprocess.CompletedProcess(
                    command.argv,
                    1,
                    stdout="",
                    stderr=f"{type(exc).__name__}: {exc}",
                )
            finally:
                command.done.set()
            if should_stop:
                break
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            logger.debug("suppressed error in %s", "_playwright_worker", exc_info=True)
        try:
            if browser is not None:
                browser.close()
        except Exception:
            logger.debug("suppressed error in %s", "_playwright_worker", exc_info=True)
        try:
            if playwright is not None:
                playwright.stop()
        except Exception:
            logger.debug("suppressed error in %s", "_playwright_worker", exc_info=True)
        session.stopped.set()
        with _PLAYWRIGHT_LOCK:
            if session.idle_timer is not None:
                session.idle_timer.cancel()
                session.idle_timer = None
            if _PLAYWRIGHT_SESSIONS.get(session.name) is session:
                _PLAYWRIGHT_SESSIONS.pop(session.name, None)


def _playwright_session(request: AssistantToolRequest) -> _PlaywrightSession:
    name = _session_name(request.session_id, proposal_id=request.proposal_id)
    with _PLAYWRIGHT_LOCK:
        current = _PLAYWRIGHT_SESSIONS.get(name)
        if current is not None and current.thread is not None and current.thread.is_alive():
            _schedule_playwright_session_expiry_locked(current)
            return current
        if current is not None:
            _PLAYWRIGHT_SESSIONS.pop(name, None)
            if current.idle_timer is not None:
                current.idle_timer.cancel()
        if len(_PLAYWRIGHT_SESSIONS) >= _MAX_PLAYWRIGHT_SESSIONS:
            raise RuntimeError("browser session capacity is full")
        session = _PlaywrightSession(name=name)
        session.thread = threading.Thread(
            target=_playwright_worker,
            args=(session,),
            name=f"omnix-playwright-{name}",
            daemon=True,
        )
        _PLAYWRIGHT_SESSIONS[name] = session
        session.thread.start()
        _schedule_playwright_session_expiry_locked(session)
        return session


def _schedule_playwright_session_expiry_locked(session: _PlaywrightSession) -> None:
    if session.idle_timer is not None:
        session.idle_timer.cancel()
    session.expires_at = time.monotonic() + _PLAYWRIGHT_SESSION_TTL_SECONDS
    timer = threading.Timer(
        _PLAYWRIGHT_SESSION_TTL_SECONDS,
        _expire_playwright_session,
        args=(session.name, session, session.expires_at),
    )
    timer.daemon = True
    session.idle_timer = timer
    timer.start()


def _expire_playwright_session(
    name: str,
    session: _PlaywrightSession,
    expected_expiry: float,
) -> None:
    with _PLAYWRIGHT_LOCK:
        if (
            _PLAYWRIGHT_SESSIONS.get(name) is not session
            or session.expires_at != expected_expiry
        ):
            return
        remaining = session.expires_at - time.monotonic()
        if remaining > 0:
            timer = threading.Timer(
                remaining,
                _expire_playwright_session,
                args=(name, session, expected_expiry),
            )
            timer.daemon = True
            session.idle_timer = timer
            timer.start()
            return
        _PLAYWRIGHT_SESSIONS.pop(name, None)
        session.idle_timer = None
    _stop_playwright_session(session)


def _playwright_request_for_command(
    request: AssistantToolRequest,
    argv: list[str],
) -> AssistantToolRequest:
    if argv and argv[-1] == "close" and request.action_id != "browser.close":
        return request.model_copy(update={"action_id": "browser.close", "input": {}})
    return request


def _run_playwright_command(
    request: AssistantToolRequest,
    argv: list[str],
    metadata: dict[str, Any],
    *,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
    session = _playwright_session(request)
    if not session.ready.wait(timeout=timeout):
        with _PLAYWRIGHT_LOCK:
            if _PLAYWRIGHT_SESSIONS.get(session.name) is session:
                _PLAYWRIGHT_SESSIONS.pop(session.name, None)
        raise subprocess.TimeoutExpired(argv, timeout)
    if session.startup_error is not None:
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr=f"Playwright browser launch failed: {session.startup_error}",
        )
    command = _PlaywrightCommand(
        request=_playwright_request_for_command(request, argv),
        argv=argv,
        metadata=metadata,
    )
    try:
        session.commands.put(command, timeout=timeout)
    except Full:
        _stop_playwright_session(session)
        raise subprocess.TimeoutExpired(argv, timeout)
    if not command.done.wait(timeout=timeout):
        _stop_playwright_session(session)
        with _PLAYWRIGHT_LOCK:
            if _PLAYWRIGHT_SESSIONS.get(session.name) is session:
                _PLAYWRIGHT_SESSIONS.pop(session.name, None)
        raise subprocess.TimeoutExpired(argv, timeout)
    if command.completed is None:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="browser command did not complete")
    return command.completed


def _stop_playwright_session(session: _PlaywrightSession) -> None:
    with _PLAYWRIGHT_LOCK:
        if session.idle_timer is not None:
            session.idle_timer.cancel()
            session.idle_timer = None
    if session.thread is None or not session.thread.is_alive() or not session.ready.is_set():
        return
    close = _PlaywrightCommand(
        request=AssistantToolRequest(
            tool_id="browser",
            action_id="browser.close",
            session_id=session.name,
        ),
        argv=["playwright", "close"],
        metadata={},
    )
    session.commands.put(close)
    close.done.wait(timeout=5)


def _stop_all_playwright_sessions() -> None:
    with _PLAYWRIGHT_LOCK:
        sessions = list(_PLAYWRIGHT_SESSIONS.values())
        _PLAYWRIGHT_SESSIONS.clear()
        for session in sessions:
            if session.idle_timer is not None:
                session.idle_timer.cancel()
                session.idle_timer = None
    for session in sessions:
        _stop_playwright_session(session)


def _log_browser_activity(
    request: AssistantToolRequest,
    backend: str,
    *,
    duration_ms: float | None = None,
    completed: subprocess.CompletedProcess[str] | None = None,
    error: BaseException | str | None = None,
) -> None:
    try:
        from app.observability.agent_logging import log_agent_activity

        fields: dict[str, Any] = {
            "action_id": request.action_id,
            "backend": backend,
            "session_id": request.session_id,
        }
        if completed is not None:
            fields["returncode"] = completed.returncode
        log_agent_activity(
            "browser.action",
            category="browser",
            run_id=_run_id_from_proposal(request.proposal_id),
            duration_ms=duration_ms,
            fields=fields,
            error=error,
        )
    except Exception:
        # Browser diagnostics must never change capability behavior.
        logger.debug("suppressed error in %s", "_log_browser_activity", exc_info=True)


def _run_browser_command(
    request: AssistantToolRequest,
    argv: list[str],
    metadata: dict[str, Any] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    backend = _browser_backend()
    started = time.monotonic()
    try:
        if backend == "playwright":
            completed = _run_playwright_command(
                request,
                argv,
                metadata or {},
                timeout_seconds=timeout_seconds,
            )
        elif timeout_seconds is None:
            completed = _run(argv)
        else:
            completed = _run(argv, timeout_seconds=timeout_seconds)
    except Exception as exc:
        _log_browser_activity(
            request,
            backend,
            duration_ms=(time.monotonic() - started) * 1_000,
            error=exc,
        )
        raise
    _log_browser_activity(
        request,
        backend,
        duration_ms=(time.monotonic() - started) * 1_000,
        completed=completed,
    )
    return completed


def _terminate_browser_process(process: subprocess.Popen[str]) -> None:
    """Terminate the CLI and any browser daemon it spawned after a timeout."""

    try:
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    shell=False,
                    check=False,
                    capture_output=True,
                    timeout=5,
                    env=_minimal_environment(),
                )
            else:
                process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                process.kill()
        try:
            process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def _run(
    argv: list[str],
    *,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
    process = subprocess.Popen(
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_minimal_environment(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_browser_process(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except (OSError, subprocess.SubprocessError):
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            argv,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from exc
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _run_id_from_request(request: AssistantToolRequest) -> str | None:
    return _run_id_from_proposal(request.proposal_id)


def _workspace_for_preview(request: AssistantToolRequest) -> tuple[str, Path, Any]:
    run_id = _run_id_from_request(request)
    if not run_id:
        raise ValueError("workspace preview requires a run-scoped broker proposal")

    # The agent runtime supplies the authoritative issued workspace (ADR-0016).
    from app.runtime.ports import optional

    from .contracts import AGENT_RUN_WORKSPACES

    workspaces = optional(AGENT_RUN_WORKSPACES)
    preview = workspaces.preview(run_id) if workspaces is not None else None
    if preview is None:
        raise ValueError("workspace preview requires an issued Agent workspace")
    workspace, launcher = preview
    root = Path(workspace.worktree or workspace.root).resolve()
    package = _preview_package_path()
    package_json = root / package / "package.json"
    if not package_json.is_file():
        raise ValueError(f"workspace preview package is missing: {package}")
    # A sandboxed run's preview runs in the sandbox too: the dev server executes
    # code the agent may have changed (WP-4.7); the launcher comes with the workspace.
    return run_id, root, launcher


def _preview_npm_command() -> str:
    configured = _env_str("OMNIX_AGENT_PREVIEW_NPM_COMMAND", "").strip()
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)
        raise ValueError("OMNIX_AGENT_PREVIEW_NPM_COMMAND does not point to a file")

    repo_root = Path(__file__).resolve().parents[4]
    patterns = (
        "node-v*-win-x64/npm.cmd",
        "node-v*-linux-x64/bin/npm",
        "node-v*-darwin-*/bin/npm",
    )
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend((repo_root / ".tools").glob(pattern))
    for candidate in sorted(candidates, key=lambda path: str(path), reverse=True):
        if candidate.is_file():
            return str(candidate)

    resolved = shutil.which("npm.cmd") or shutil.which("npm")
    if not resolved:
        raise ValueError("npm is unavailable for the Omnix workspace preview")
    return resolved


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_preview(process: subprocess.Popen[str], port: int, *, http: bool = False) -> bool:
    """Wait until the preview listens; ``http`` waits for an HTTP answer instead.

    A sandboxed preview sits behind a relay that accepts connections before
    the dev server listens, so only an HTTP response proves it is up.
    """
    deadline = time.monotonic() + _preview_start_timeout_seconds()
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if http:
                import urllib.error
                import urllib.request

                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2):  # noqa: S310 - loopback preview
                        return True
                except urllib.error.HTTPError:
                    return True
            else:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    return True
        except OSError:
            time.sleep(0.25 if http else 0.1)
    return False


def _terminate_preview_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    shell=False,
                    check=False,
                    capture_output=True,
                    timeout=5,
                    env=_minimal_environment(),
                )
            else:
                process.terminate()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    process.kill()
            else:
                process.kill()
            process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def _terminate_preview(preview: _WorkspacePreview) -> None:
    _terminate_preview_process(preview.process)
    if preview.cleanup is not None:
        preview.cleanup()


def _expire_workspace_preview(run_id: str, pid: int) -> None:
    with _PREVIEW_LOCK:
        preview = _PREVIEWS.get(run_id)
        if preview is None or preview.process.pid != pid:
            return
        _PREVIEWS.pop(run_id, None)
    _terminate_preview(preview)


def _prune_expired_workspace_previews() -> None:
    now = time.monotonic()
    with _PREVIEW_LOCK:
        expired = [
            (run_id, preview)
            for run_id, preview in _PREVIEWS.items()
            if preview.expires_at and now >= preview.expires_at
        ]
        for run_id, _preview in expired:
            _PREVIEWS.pop(run_id, None)
    for _run_id, preview in expired:
        if preview.timer is not None:
            preview.timer.cancel()
        _terminate_preview(preview)


def _stop_workspace_preview(request: AssistantToolRequest) -> bool:
    run_id = _run_id_from_request(request)
    if not run_id:
        return False
    with _PREVIEW_LOCK:
        preview = _PREVIEWS.pop(run_id, None)
    if preview is None:
        return False
    if preview.timer is not None:
        preview.timer.cancel()
    _terminate_preview(preview)
    return True


def _stop_all_workspace_previews() -> None:
    with _PREVIEW_LOCK:
        previews = list(_PREVIEWS.values())
        _PREVIEWS.clear()
    for preview in previews:
        if preview.timer is not None:
            preview.timer.cancel()
        _terminate_preview(preview)


def _start_workspace_preview(
    request: AssistantToolRequest,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    run_id, root, launcher = _workspace_for_preview(request)
    package = _preview_package_path()
    route = str(payload.get("path") or "/").strip()
    if not _PREVIEW_ROUTE.fullmatch(route) or "\\" in route:
        raise ValueError("workspace preview path must be a bounded absolute browser route")

    _stop_workspace_preview(request)
    _prune_expired_workspace_previews()
    npm = _preview_npm_command() if launcher is None else ""
    last_error = "workspace preview failed to become ready"
    for _attempt in range(3):
        port = _allocate_loopback_port()
        cleanup: Callable[[], None] | None = None
        argv = [
            npm,
            "--prefix",
            package,
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--strictPort",
        ]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(root),
            "env": _preview_environment(),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
            "text": True,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True
        try:
            if launcher is not None:
                process, cleanup = launcher(root=root, package=package, port=port)
            else:
                process = subprocess.Popen(argv, **popen_kwargs)
        except OSError as exc:
            raise ValueError(f"workspace preview could not start npm: {type(exc).__name__}") from exc

        if not _wait_for_preview(process, port, http=launcher is not None):
            code = process.poll()
            _terminate_preview(_WorkspacePreview(process=process, url="", port=port, cleanup=cleanup))
            last_error = (
                f"workspace preview process exited with code {code}"
                if code is not None
                else "workspace preview did not listen before the startup timeout"
            )
            continue

        base_url = f"http://127.0.0.1:{port}"
        url = f"{base_url}{route}"
        preview = _WorkspacePreview(process=process, url=base_url, port=port, cleanup=cleanup)
        preview_ttl = _preview_ttl_seconds()
        preview.expires_at = time.monotonic() + preview_ttl
        timer = threading.Timer(
            preview_ttl,
            _expire_workspace_preview,
            args=(run_id, process.pid),
        )
        timer.daemon = True
        preview.timer = timer
        with _PREVIEW_LOCK:
            capacity_reached = len(_PREVIEWS) >= _MAX_PREVIEW_ENTRIES
            if not capacity_reached:
                _PREVIEWS[run_id] = preview
        if capacity_reached:
            timer.cancel()
            _terminate_preview(preview)
            raise ValueError("workspace preview capacity is full")
        timer.start()
        return url, {
            "workspace_preview": True,
            "workspace_preview_url": base_url,
            "workspace_preview_port": port,
            "workspace_preview_path": route,
            "workspace_preview_package": package,
            "workspace_preview_sandboxed": launcher is not None,
        }

    raise ValueError(last_error)


def _best_effort_validation_cleanup(request: AssistantToolRequest) -> dict[str, bool]:
    preview_stopped = _stop_workspace_preview(request)
    if not preview_stopped:
        return {
            "workspace_preview_stopped": False,
            "browser_closed": False,
        }
    browser_closed = _best_effort_browser_close(request)
    _clear_browser_session_generation(request)
    return {
        "workspace_preview_stopped": True,
        "browser_closed": browser_closed,
    }


def _best_effort_browser_close(request: AssistantToolRequest) -> bool:
    try:
        completed = _run_browser_command(
            request,
            _base_argv(request) + ["close"],
            timeout_seconds=5,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _clear_browser_session_generation(request: AssistantToolRequest) -> None:
    with _BROWSER_SESSION_LOCK:
        _BROWSER_SESSION_GENERATIONS.pop(
            _browser_session_key(request.session_id, request.proposal_id),
            None,
        )


def _rotate_browser_session(request: AssistantToolRequest) -> None:
    key = _browser_session_key(request.session_id, request.proposal_id)
    with _BROWSER_SESSION_LOCK:
        generation = _browser_session_generation_locked(key, time.monotonic()) + 1
        _BROWSER_SESSION_GENERATIONS[key] = (
            generation,
            time.monotonic(),
        )
        _BROWSER_SESSION_GENERATIONS.move_to_end(key)
        while len(_BROWSER_SESSION_GENERATIONS) > _MAX_BROWSER_SESSION_GENERATIONS:
            _BROWSER_SESSION_GENERATIONS.popitem(last=False)


def _reset_failed_workspace_preview(request: AssistantToolRequest) -> None:
    """Drop a broken run-scoped daemon before retrying a preview open."""

    try:
        _run_browser_command(
            request,
            _base_argv(request) + ["close"],
            timeout_seconds=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    _stop_workspace_preview(request)
    _rotate_browser_session(request)


def _command_for(request: AssistantToolRequest) -> tuple[list[str], dict[str, Any]]:
    action = request.action_id
    payload = dict(request.input)
    argv = _base_argv(request)
    metadata: dict[str, Any] = {}

    if action == "browser.open":
        if payload.get("workspace_preview") is True:
            url, preview_metadata = _start_workspace_preview(request, payload)
            metadata.update(preview_metadata)
        else:
            url = _validate_open_url(payload.get("url"))
        argv.extend(["open", url])
        metadata["url"] = url
    elif action == "browser.snapshot":
        argv.extend(["snapshot", "--json"])
    elif action == "browser.click":
        argv.extend(["click", _safe_selector(payload.get("selector"))])
    elif action == "browser.fill":
        argv.extend([
            "fill",
            _safe_selector(payload.get("selector")),
            _safe_text(payload.get("text"), field="text"),
        ])
    elif action == "browser.press":
        argv.extend(["press", _safe_text(payload.get("key"), field="key", max_chars=128)])
    elif action == "browser.hover":
        argv.extend(["hover", _safe_selector(payload.get("selector"))])
    elif action == "browser.select":
        argv.extend([
            "select",
            _safe_selector(payload.get("selector")),
            _safe_text(payload.get("value"), field="value", max_chars=2048),
        ])
    elif action == "browser.scroll":
        direction = str(payload.get("direction") or "down").strip().casefold()
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("browser.scroll direction must be up/down/left/right")
        argv.extend(["scroll", direction])
        pixels = payload.get("pixels")
        if pixels is not None:
            amount = int(pixels)
            if amount < 1 or amount > 100_000:
                raise ValueError("browser.scroll pixels outside allowed range")
            argv.append(str(amount))
    elif action == "browser.wait":
        supplied = [
            key for key in ("selector", "text", "url", "milliseconds", "load")
            if payload.get(key) not in {None, ""}
        ]
        if len(supplied) != 1:
            raise ValueError("browser.wait requires exactly one bounded wait condition")
        kind = supplied[0]
        if kind == "selector":
            argv.extend(["wait", _safe_selector(payload[kind])])
        elif kind == "text":
            argv.extend(["wait", "--text", _safe_text(payload[kind], field="text", max_chars=4096)])
        elif kind == "url":
            pattern = _safe_text(payload[kind], field="url pattern", max_chars=2048)
            argv.extend(["wait", "--url", pattern])
        elif kind == "load":
            state = str(payload[kind]).strip().casefold()
            if state not in {"load", "domcontentloaded", "networkidle"}:
                raise ValueError("browser.wait load state is not allowed")
            argv.extend(["wait", "--load", state])
        else:
            milliseconds = int(payload[kind])
            if milliseconds < 0 or milliseconds > 30_000:
                raise ValueError("browser.wait milliseconds outside allowed range")
            argv.extend(["wait", str(milliseconds)])
    elif action == "browser.get_text":
        argv.extend(["get", "text", _safe_selector(payload.get("selector"))])
    elif action == "browser.get_attribute":
        argv.extend([
            "get",
            "attr",
            _safe_selector(payload.get("selector")),
            _safe_text(payload.get("attribute"), field="attribute", max_chars=256),
        ])
    elif action == "browser.get_url":
        argv.extend(["get", "url"])
    elif action == "browser.screenshot":
        directory = Path(tempfile.gettempdir()) / "omnix-agent-browser"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (
            f"{_session_name(request.session_id, proposal_id=request.proposal_id)}-"
            f"{os.urandom(6).hex()}.png"
        )
        argv.extend(["screenshot", str(target)])
        if bool(payload.get("full_page")):
            argv.append("--full")
        metadata["screenshot_path"] = str(target)
    elif action == "browser.assert_text_contains":
        argv.extend(["get", "text", _safe_selector(payload.get("selector"))])
        metadata["assertion_expected"] = _safe_text(
            payload.get("expected"), field="expected text", max_chars=4096
        )
    elif action == "browser.assert_text_not_contains":
        argv.extend(["get", "text", _safe_selector(payload.get("selector"))])
        metadata["assertion_expected"] = _safe_text(
            payload.get("expected"), field="forbidden text", max_chars=4096
        )
    elif action == "browser.assert_attribute_contains":
        argv.extend([
            "get",
            "attr",
            _safe_selector(payload.get("selector")),
            _safe_text(payload.get("attribute"), field="attribute", max_chars=256),
        ])
        metadata["assertion_expected"] = _safe_text(
            payload.get("expected"), field="expected attribute", max_chars=4096
        )
    elif action == "browser.assert_url_contains":
        argv.extend(["get", "url"])
        metadata["assertion_expected"] = _safe_text(
            payload.get("expected"), field="expected URL", max_chars=4096
        )
    elif action == "browser.close":
        argv.append("close")
    else:
        raise ValueError("unsupported browser capability")
    return argv, metadata


def _run_workspace_preview_open(
    request: AssistantToolRequest,
) -> tuple[list[str], dict[str, Any], subprocess.CompletedProcess[str]]:
    """Open a preview once more with a fresh daemon after a CDP failure."""

    argv, metadata = _command_for(request)
    try:
        completed = _run_browser_command(request, argv, metadata)
    except (OSError, subprocess.SubprocessError):
        _reset_failed_workspace_preview(request)
        argv, metadata = _command_for(request)
        return argv, metadata, _run_browser_command(request, argv, metadata)
    if completed.returncode == 0:
        return argv, metadata, completed

    _reset_failed_workspace_preview(request)
    argv, metadata = _command_for(request)
    return argv, metadata, _run_browser_command(request, argv, metadata)


def run_browser_tool_request(request: AssistantToolRequest) -> AssistantToolResult:
    if request.action_id not in _BROWSER_ACTIONS:
        return AssistantToolResult(
            tool_id="browser",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="Unsupported governed browser action.",
            error="unsupported_browser_action",
        )
    if not browser_available():
        return AssistantToolResult(
            tool_id="browser",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="agent-browser is not installed or is disabled.",
            error="browser_runtime_unavailable",
        )
    try:
        if request.action_id == "browser.open" and request.input.get("workspace_preview") is True:
            argv, metadata, completed = _run_workspace_preview_open(request)
        else:
            argv, metadata = _command_for(request)
            completed = _run_browser_command(request, argv, metadata)
    except (ValueError, TypeError) as exc:
        if request.action_id == "browser.open" and request.input.get("workspace_preview") is True:
            _stop_workspace_preview(request)
        return AssistantToolResult(
            tool_id="browser",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="Browser request was rejected by Omnix policy.",
            error=f"browser_policy_rejected:{exc}",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if request.action_id == "browser.open" and request.input.get("workspace_preview") is True:
            _stop_workspace_preview(request)
            _best_effort_browser_close(request)
            _clear_browser_session_generation(request)
        return AssistantToolResult(
            tool_id="browser",
            action_id=request.action_id,
            session_id=request.session_id,
            result_summary="agent-browser failed to execute.",
            error=f"browser_runtime_error:{type(exc).__name__}",
        )

    stdout = (completed.stdout or "")[:_MAX_OUTPUT_CHARS]
    stderr = (completed.stderr or "")[:8_000]
    if completed.returncode != 0:
        if metadata.get("workspace_preview") is True:
            _stop_workspace_preview(request)
            _best_effort_browser_close(request)
            _clear_browser_session_generation(request)
        return AssistantToolResult(
            tool_id="browser",
            action_id=request.action_id,
            session_id=request.session_id,
            state_changed=False,
            result_summary=f"Browser action failed with exit code {completed.returncode}.",
            output={"stdout": stdout, "stderr": stderr, **metadata},
            error="browser_command_failed",
        )

    output: dict[str, Any] = {"stdout": stdout, **metadata}
    if request.action_id in _ASSERTIONS:
        expected = str(metadata.get("assertion_expected") or "")
        assertion_failed = (
            expected in stdout
            if request.action_id == "browser.assert_text_not_contains"
            else expected not in stdout
        )
        if assertion_failed:
            return AssistantToolResult(
                tool_id="browser",
                action_id=request.action_id,
                session_id=request.session_id,
                state_changed=False,
                result_summary=f"Browser assertion failed for {request.action_id}.",
                output=output,
                error="browser_assertion_failed",
            )
        output["assertion_passed"] = True
        # A passing deterministic assertion is the terminal proof required by
        # coding validation. Cleanup applies only when this run owns a workspace
        # preview; unrelated governed browser sessions retain their old behavior.
        output["cleanup"] = _best_effort_validation_cleanup(request)
    if request.action_id == "browser.snapshot" and stdout.strip():
        try:
            output["snapshot"] = json.loads(stdout)
        except json.JSONDecodeError:
            pass
    screenshot_path = metadata.get("screenshot_path")
    if isinstance(screenshot_path, str):
        target = Path(screenshot_path)
        if target.is_file():
            data = target.read_bytes()
            output.update({
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    if request.action_id == "browser.close":
        output["workspace_preview_stopped"] = _stop_workspace_preview(request)

    return AssistantToolResult(
        tool_id="browser",
        action_id=request.action_id,
        session_id=request.session_id,
        state_changed=request.action_id in _INTERACTIVE,
        result_summary=f"Completed governed browser action {request.action_id}.",
        output=output,
    )


atexit.register(_stop_all_workspace_previews)
atexit.register(_stop_all_playwright_sessions)
