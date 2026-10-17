from __future__ import annotations

import logging

from app.config.env import environment

import math
import os
import sys
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from app.composition.launcher.config import load_launcher_config
from app.runtime.net import bind_host
from app.security.service_credentials import initialize_service_token

logger = logging.getLogger(__name__)

LAUNCHER_MANAGER_VERSION = "omnix_launcher_service_manager_v1"
DEFAULT_LOG_LIMIT = 1200
DEFAULT_GATEWAY_READY_TIMEOUT_SECONDS = 420.0
GATEWAY_READY_TIMEOUT_ENV = "OMNIX_GATEWAY_STARTUP_TIMEOUT_SECONDS"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _env_flag(name: str, default: str = "0") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


def _gateway_ready_timeout_seconds() -> float:
    raw_value = environment().get(GATEWAY_READY_TIMEOUT_ENV)
    if raw_value is None:
        return DEFAULT_GATEWAY_READY_TIMEOUT_SECONDS
    try:
        timeout_s = float(raw_value)
    except ValueError:
        return DEFAULT_GATEWAY_READY_TIMEOUT_SECONDS
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        return DEFAULT_GATEWAY_READY_TIMEOUT_SECONDS
    return timeout_s


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _url_port(value: str, default: int) -> int:
    try:
        return int(urlparse(value).port or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ServiceSpec:
    service_id: str
    label: str
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    ports: tuple[int, ...] = ()
    optional: bool = False
    enabled: bool = True
    auto_start: bool = True
    description: str = ""
    startup_critical: bool = True


@dataclass
class ManagedProcess:
    spec: ServiceSpec
    process: subprocess.Popen[str] | None = None
    started_at: float = 0.0
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=DEFAULT_LOG_LIMIT))
    last_returncode: int | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def status(self) -> str:
        with self.lock:
            if not self.spec.enabled:
                return "disabled"
            if self.process is None:
                return "stopped"
            returncode = self.process.poll()
            if returncode is None:
                return "running"
            self.last_returncode = int(returncode)
            self.process = None
            return "exited"

    def snapshot(self) -> dict[str, Any]:
        status = self.status()
        with self.lock:
            return {
                "id": self.spec.service_id,
                "label": self.spec.label,
                "status": status,
                "enabled": bool(self.spec.enabled),
                "optional": bool(self.spec.optional),
                "auto_start": bool(self.spec.auto_start),
                "description": self.spec.description,
                "pid": self.process.pid if self.process is not None and status == "running" else None,
                "started_at": self.started_at if self.started_at else None,
                "uptime_seconds": max(0.0, time.time() - self.started_at) if self.started_at and status == "running" else 0.0,
                "last_returncode": self.last_returncode,
                "recent_logs": list(self.logs)[-80:],
            }


class LauncherServiceManager:
    def __init__(self, specs: list[ServiceSpec], *, log_limit: int = DEFAULT_LOG_LIMIT) -> None:
        self._services: dict[str, ManagedProcess] = {
            spec.service_id: ManagedProcess(spec=spec, logs=deque(maxlen=log_limit)) for spec in specs
        }
        self._lock = threading.RLock()

    def list_services(self) -> list[dict[str, Any]]:
        return [service.snapshot() for service in self._services.values()]

    def service_snapshot(self, service_id: str) -> dict[str, Any]:
        return self._service(service_id).snapshot()

    def logs(self, service_id: str, *, limit: int = 300) -> list[str]:
        service = self._service(service_id)
        with service.lock:
            return list(service.logs)[-max(1, min(2000, int(limit or 300))):]

    def _run_release_migrations(self) -> dict[str, Any]:
        gateway = self._services.get("gateway")
        if gateway is None:
            return {"ok": True, "skipped": True}
        python = gateway.spec.command[0]
        env = environment().copy()
        env.update(gateway.spec.env)
        env["OMNIX_SERVICE_TOKEN"] = initialize_service_token()
        self._append(gateway, "[launcher] applying PostgreSQL migrations")
        result = subprocess.run(
            [python, "-m", "app.persistence", "migrate"],
            cwd=str(gateway.spec.cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                self._append(gateway, "[migrate] " + line)
        if result.stderr:
            for line in result.stderr.splitlines():
                self._append(gateway, "[migrate] " + line)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
        }

    def start_auto_services(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        migration = self._run_release_migrations()
        results["migrate"] = migration
        if not migration.get("ok"):
            return {
                "format_version": LAUNCHER_MANAGER_VERSION,
                "started": results,
                "starting_in_background": [],
            }
        automatic = [
            (service_id, service)
            for service_id, service in self._services.items()
            if service.spec.enabled and service.spec.auto_start
        ]
        critical = [item for item in automatic if item[1].spec.startup_critical]
        deferred = [item for item in automatic if not item[1].spec.startup_critical]
        priority = {"gateway": 0, "web": 1}
        critical.sort(key=lambda item: priority.get(item[0], 2))

        for service_id, service in critical:
            results[service_id] = self.start(service_id)
            if service_id == "gateway" and results[service_id].get("ok"):
                timeout_s = _gateway_ready_timeout_seconds()
                ready = _wait_for_port_open(
                    service.spec.ports[0] if service.spec.ports else 8000,
                    timeout_s=timeout_s,
                )
                results[service_id]["ready"] = ready
                if not ready:
                    self._append(
                        service,
                        "[launcher] gateway did not become reachable within "
                        f"{timeout_s:g}s; web startup will retry readiness",
                    )

        deferred_ids = [service_id for service_id, _service in deferred]
        if deferred_ids:
            for _service_id, service in deferred:
                self._append(service, "[launcher] queued until gateway and web startup completes")
            web = self._services.get("web")
            web_result = results.get("web")
            web_port = (
                web.spec.ports[0]
                if web and web.spec.ports and web_result and web_result.get("ok")
                else None
            )
            threading.Thread(
                target=self._start_deferred_services,
                args=(deferred_ids, web_port),
                name="omnix-deferred-service-startup",
                daemon=True,
            ).start()

        return {
            "format_version": LAUNCHER_MANAGER_VERSION,
            "started": results,
            "starting_in_background": deferred_ids,
        }

    def _start_deferred_services(
        self, service_ids: list[str], web_port: int | None,
    ) -> None:
        if web_port is not None and not _wait_for_port_open(web_port, timeout_s=120.0):
            for service_id in service_ids:
                self._append(
                    self._service(service_id),
                    "[launcher] web did not become reachable before deferred startup",
                )
        for service_id in service_ids:
            try:
                result = self.start(service_id)
                if not result.get("ok"):
                    self._append(
                        self._service(service_id),
                        f"[launcher] deferred startup failed: {result.get('error', 'unknown error')}",
                    )
            except Exception as exc:
                self._append(
                    self._service(service_id),
                    f"[launcher] deferred startup failed: {type(exc).__name__}: {exc}",
                )

    def start(self, service_id: str) -> dict[str, Any]:
        service = self._service(service_id)
        with service.lock:
            if not service.spec.enabled:
                self._append(service, "[launcher] service disabled; not starting")
                return {"ok": False, "error": "service_disabled", "service": service.snapshot()}
            if service.process is not None and service.process.poll() is None:
                return {"ok": True, "already_running": True, "service": service.snapshot()}
            if service_id == "web":
                gateway_ready, gateway_result = self._ensure_gateway_ready()
                if not gateway_ready:
                    self._append(service, "[launcher] gateway is not reachable; web service was not started")
                    return {
                        "ok": False,
                        "error": "gateway_not_ready",
                        "gateway": gateway_result,
                        "service": service.snapshot(),
                    }
            env = environment().copy()
            env.update(service.spec.env)
            if service_id == "web":
                env.pop("OMNIX_SERVICE_TOKEN", None)
            else:
                env["OMNIX_SERVICE_TOKEN"] = initialize_service_token()
            # Only gateway processes sign or verify agent run tokens; the
            # gateway entrypoint loads the key itself (WP-4.6).
            env.pop("OMNIX_RUN_TOKEN_KEY", None)
            # Semantic v2 is the only typed-chat production router. Do not pass
            # the retired shadow/legacy-v1 switch to launcher-managed services,
            # even if it remains in a user's parent shell.
            env.pop("OMNIX_AGENT_SEMANTIC_ROUTING_MODE", None)
            service.spec.cwd.mkdir(parents=True, exist_ok=True)
            self._clear_conflicting_ports(service)
            self._append(service, "[launcher] starting: " + " ".join(service.spec.command))
            # A process group of its own, so stop() can send it CTRL_BREAK.
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                process = subprocess.Popen(
                    service.spec.command,
                    cwd=str(service.spec.cwd),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                )
            except OSError as exc:
                # A missing interpreter or tool (npm, a Conda environment) is a
                # failed start the dashboard shows, not a launcher error.
                self._append(service, f"[launcher] could not start {service.spec.command[0]}: {exc.strerror or exc}")
                return {"ok": False, "error": "executable_unavailable", "service": service.snapshot()}
            service.process = process
            service.started_at = time.time()
            service.last_returncode = None
            thread = threading.Thread(
                target=self._pump_logs_for_process,
                args=(service, process),
                daemon=True,
            )
            thread.start()
            return {"ok": True, "service": service.snapshot()}

    def _ensure_gateway_ready(self) -> tuple[bool, dict[str, Any]]:
        """Start the gateway and wait for it before launching the Vite proxy."""
        gateway = self._services.get("gateway")
        if gateway is None or not gateway.spec.enabled:
            return True, {"ok": True, "skipped": True}

        result = self.start("gateway")
        if not result.get("ok"):
            return False, result

        port = gateway.spec.ports[0] if gateway.spec.ports else 8000
        if _wait_for_port_open(
            port,
            timeout_s=_gateway_ready_timeout_seconds(),
        ):
            return True, result
        return False, {
            "ok": False,
            "error": "gateway_not_ready",
            "service": gateway.snapshot(),
        }

    def stop(self, service_id: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
        service = self._service(service_id)
        with service.lock:
            process = service.process
            if process is None or process.poll() is not None:
                service.process = None
                return {"ok": True, "already_stopped": True, "service": service.snapshot()}
            self._append(service, "[launcher] stopping")
            try:
                if sys.platform == "win32":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
                process.wait(timeout=timeout_s)
            except Exception:
                self._append(service, "[launcher] terminate timed out; killing")
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception as exc:
                    self._append(service, f"[launcher] kill failed: {type(exc).__name__}: {exc}")
            service.last_returncode = process.poll()
            service.process = None
            return {"ok": True, "service": service.snapshot()}

    def restart(self, service_id: str) -> dict[str, Any]:
        stop_result = self.stop(service_id)
        start_result = self.start(service_id)
        return {"ok": bool(stop_result.get("ok")) and bool(start_result.get("ok")), "stop": stop_result, "start": start_result}

    def stop_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for service_id in reversed(list(self._services.keys())):
            results[service_id] = self.stop(service_id)
        return {"ok": True, "stopped": results}

    def _service(self, service_id: str) -> ManagedProcess:
        service = self._services.get(service_id)
        if service is None:
            raise KeyError(f"unknown_service:{service_id}")
        return service

    def _append(self, service: ManagedProcess, line: str) -> None:
        with service.lock:
            stamp = time.strftime("%H:%M:%S")
            service.logs.append(f"[{stamp}] {line.rstrip()}")

    def _clear_conflicting_ports(self, service: ManagedProcess) -> None:
        for port in service.spec.ports:
            killed = _kill_processes_for_port(port)
            if killed:
                self._append(service, f"[launcher] stopped conflicting process(es) on port {port}: {', '.join(str(pid) for pid in killed)}")
                if not _wait_for_port_release(port):
                    owners = _find_port_owner_pids(port)
                    if owners:
                        self._append(service, f"[launcher] warning: port {port} is still owned by: {', '.join(str(pid) for pid in owners)}")
                    else:
                        self._append(service, f"[launcher] warning: port {port} did not become bindable after cleanup")

    def _pump_logs_for_process(
        self,
        service: ManagedProcess,
        process: subprocess.Popen[str],
    ) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._append(service, line)
        finally:
            with service.lock:
                if process.poll() is not None:
                    if service.process is process:
                        service.last_returncode = process.returncode
                        service.process = None
                    self._append(service, f"[launcher] exited with code {process.returncode}")


def _is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def _wait_for_port_open(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout_s: float = DEFAULT_GATEWAY_READY_TIMEOUT_SECONDS,
    interval_s: float = 0.25,
) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        try:
            with socket.create_connection((host, int(port)), timeout=0.25):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(max(0.05, float(interval_s)))


def _wait_for_port_release(port: int, *, timeout_s: float = 8.0, interval_s: float = 0.25) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if _is_port_available(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, float(interval_s)))


def _find_port_owner_pids(port: int) -> list[int]:
    if os.name != "nt":
        return []
    script = (
        f"Get-NetTCPConnection -LocalPort {int(port)} -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []

    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid > 0 and pid != os.getpid() and pid not in pids:
            pids.append(pid)
    return pids


def _kill_processes_for_port(port: int) -> list[int]:
    killed: list[int] = []
    for pid in _find_port_owner_pids(port):
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            logger.debug("suppressed error in %s", "_kill_processes_for_port", exc_info=True)
            continue
        killed.append(pid)
    return killed


def build_default_service_specs(root: Path | None = None) -> list[ServiceSpec]:
    root = root or _repo_root()
    host = bind_host()
    launcher = load_launcher_config(root)
    app_python = launcher.python("app")
    tts_python = launcher.python("tts")
    stt_python = launcher.python("stt")
    image_enabled = _env_flag("OMNIX_IMAGE_ENABLED")
    image_auto_start = image_enabled and _env_flag("OMNIX_START_IMAGE_SERVICE")
    hermes_enabled = _env_flag("HERMES_ENABLED")
    hermes_auto_start = hermes_enabled and _env_flag("OMNIX_START_HERMES")
    hermes_base_url = environment().get("HERMES_BASE_URL", "http://127.0.0.1:8642")
    trading_hermes_enabled = environment().get(
        "OMNIX_TRADING_HERMES_RESEARCH_ENABLED",
        "1" if hermes_enabled else "0",
    )
    common = {
        "OMNIX_BIND_HOST": host,
        "PYTHONPATH": str(root / "src"),
        "OMNIX_TTS_URL": environment().get("OMNIX_TTS_URL", "http://127.0.0.1:5101"),
        "OMNIX_STT_URL": environment().get("OMNIX_STT_URL", "http://127.0.0.1:5201"),
        "OMNIX_IMAGE_ENABLED": environment().get("OMNIX_IMAGE_ENABLED", "0"),
        "OMNIX_IMAGE_URL": "http://127.0.0.1:5301" if image_enabled else "",
        "OMNIX_CHARACTER_MODE_ENABLED": environment().get("OMNIX_CHARACTER_MODE_ENABLED", "1"),
        "OMNIX_LAUNCHER_KILL_PORT": "1",
    }
    tts_model_dir = environment().get("OMNIX_TTS_MODEL_DIR", str(root / "resources" / "models" / "tts" / "Qwen3-TTS-12Hz-0.6B-Base"))
    return [
        ServiceSpec(
            service_id="stt",
            label="Parakeet STT",
            command=[stt_python, "-m", "services.stt.parakeet_stt_server"],
            cwd=root,
            env=dict(common),
            ports=(5201,),
            description="Speech-to-text websocket service on 127.0.0.1:5201.",
            startup_critical=False,
        ),
        ServiceSpec(
            service_id="tts",
            label="Omnix TTS",
            command=[tts_python, "-m", "services.tts.tts_server"],
            cwd=root,
            env={**common, "OMNIX_TTS_MODEL_DIR": tts_model_dir, "OMNIX_QWEN3_TTS_MODEL_DIR": tts_model_dir},
            ports=(5101,),
            description="Text-to-speech service on 127.0.0.1:5101.",
            startup_critical=False,
        ),
        ServiceSpec(
            service_id="gateway",
            label="Omnix Gateway",
            command=[
                app_python,
                str(root / "scripts" / "run_omnix_gateway.py"),
                "--app",
                "app.composition.gateway.runtime_app:app",
                "--host",
                host,
                "--port",
                "8000",
            ],
            cwd=root,
            env={
                **common,
                # The local launcher is rooted in this checkout, so its gateway
                # has an operator-configured default coding workspace even when
                # a Chat turn does not explicitly attach a Local folder.
                "OMNIX_AGENT_DEFAULT_REPOSITORY": environment().get(
                    "OMNIX_AGENT_DEFAULT_REPOSITORY",
                    str(root),
                ),
                "OMNIX_TTS_MODEL_DIR": tts_model_dir,
                "OMNIX_QWEN3_TTS_MODEL_DIR": tts_model_dir,
                "HERMES_ENABLED": "1" if hermes_enabled else "0",
                "HERMES_BASE_URL": hermes_base_url,
                "OMNIX_TRADING_HERMES_RESEARCH_ENABLED": trading_hermes_enabled,
                "OMNIX_AGENT_DEBUG_LOGS": environment().get(
                    "OMNIX_AGENT_DEBUG_LOGS", "0"
                ),
                "OMNIX_AGENT_LOG_DIR": environment().get(
                    "OMNIX_AGENT_LOG_DIR",
                    str(root / "resources" / "logs" / "agent"),
                ),
                "OMNIX_AGENT_LOG_RETENTION_DAYS": environment().get(
                    "OMNIX_AGENT_LOG_RETENTION_DAYS", "30"
                ),
                "OMNIX_AGENT_LOG_MAX_FIELD_CHARS": environment().get(
                    "OMNIX_AGENT_LOG_MAX_FIELD_CHARS", "12000"
                ),
                # The installed Windows agent-browser daemon currently loses
                # its CDP response channel on this host. Keep the governed
                # Playwright backend as the launcher default, with an explicit
                # override available for environments using a healthy daemon.
                "OMNIX_AGENT_BROWSER_BACKEND": environment().get(
                    "OMNIX_AGENT_BROWSER_BACKEND",
                    "playwright" if os.name == "nt" else "agent-browser",
                ),
            },
            ports=(8000,),
            description="FastAPI gateway for the redesigned web app on 127.0.0.1:8000.",
        ),
        ServiceSpec(
            service_id="web",
            label="Omnix Web App",
            command=[_npm_command(), "run", "web:dev", "--", "--host", host],
            cwd=root,
            env=dict(common),
            ports=(5173,),
            description="React/Vite browser app on 127.0.0.1:5173.",
        ),
        ServiceSpec(
            service_id="hermes",
            label="Hermes Gateway",
            command=["hermes", "gateway"],
            cwd=root,
            env={**common, "HERMES_BASE_URL": hermes_base_url},
            ports=(_url_port(hermes_base_url, 8642),),
            optional=True,
            enabled=hermes_enabled,
            auto_start=hermes_auto_start,
            startup_critical=False,
            description=(
                f"Optional Hermes messaging and planner gateway on {hermes_base_url}."
            ),
        ),
        ServiceSpec(
            service_id="image",
            label="Image Service",
            command=[app_python, "-m", "uvicorn", "services.image.image_service_app:app", "--host", host, "--port", "5301"],
            cwd=root,
            env={
                **common,
                "OMNIX_IMAGE_ENABLED": "1",
                "OMNIX_IMAGE_SERVICE_MODE": "1",
                "OMNIX_IMAGE_PRELOAD": environment().get("OMNIX_IMAGE_PRELOAD", "0"),
                "OMNIX_IMAGE_WARMUP": environment().get("OMNIX_IMAGE_WARMUP", "0"),
                "OMNIX_IMAGE_URL": "",
            },
            ports=(5301,),
            optional=True,
            enabled=image_enabled,
            auto_start=image_auto_start,
            startup_critical=False,
            description=(
                "Optional lightweight image generation service. It can be started manually "
                "without downloading or loading model weights."
            ),
        ),
    ]


_manager: LauncherServiceManager | None = None


def get_default_manager() -> LauncherServiceManager:
    global _manager
    if _manager is None:
        _manager = LauncherServiceManager(build_default_service_specs())
    return _manager


def reset_default_manager_for_tests(manager: LauncherServiceManager | None = None) -> None:
    global _manager
    _manager = manager
