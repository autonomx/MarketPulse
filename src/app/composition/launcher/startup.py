"""What ``python -m app.composition.launcher start`` does before serving the dashboard (WP-11.4).

The Python launcher is the one source of truth for starting Omnix; the
``start_all.bat`` and ``start_all.sh`` wrappers only find the interpreter (and
on Windows load the protected database credential) and call it. This module
holds the launcher's defaults and checks:

- the service environment: fixed local service URLs, then defaults an
  operator may override by setting the variable first;
- a second launcher refuses to start while the dashboard port answers;
- the TTS and STT interpreters exist and run Python 3.11;
- an existing PostgreSQL container is started and waited for until healthy.
"""
from __future__ import annotations

import glob
import os
import socket
import subprocess
import time
from collections.abc import Callable, MutableMapping
from pathlib import Path

from app.config.env import environment

from .config import LauncherConfig

LAUNCHER_URL = "http://127.0.0.1:5055"
WEB_URL = "http://127.0.0.1:5173/"

# Local service addresses the launcher always uses.
FIXED_ENVIRONMENT: tuple[tuple[str, str], ...] = (
    ("OMNIX_TTS_URL", "http://127.0.0.1:5101"),
    ("OMNIX_STT_URL", "http://127.0.0.1:5201"),
    ("OMNIX_GATEWAY_URL", "http://127.0.0.1:8000"),
    ("OMNIX_LAUNCHER_URL", LAUNCHER_URL),
)

# Defaults; a variable set before the launcher starts wins.
DEFAULT_ENVIRONMENT: tuple[tuple[str, str], ...] = (
    ("OMNIX_GATEWAY_STARTUP_TIMEOUT_SECONDS", "420"),
    ("OMNIX_APP_OPEN_URL", "http://localhost:5173/"),
    ("OMNIX_POSTGRES_START_WAIT_ATTEMPTS", "30"),
    ("OMNIX_LAUNCHER_AUTO_START", "1"),
    ("OMNIX_LAUNCHER_OPEN_BROWSER", "1"),
    ("OMNIX_BIND_HOST", "127.0.0.1"),
    # The proposal-only live agent pilot.
    ("HERMES_ENABLED", "1"),
    ("HERMES_BASE_URL", "http://127.0.0.1:8642"),
    ("OMNIX_TRADING_HERMES_RESEARCH_ENABLED", "1"),
    ("OMNIX_START_HERMES", "1"),
    ("OMNIX_LIVE_AGENT_ENABLED", "1"),
    ("OMNIX_LIVE_AGENT_AUTO_ROUTE_ENABLED", "1"),
    ("OMNIX_LIVE_AGENT_REQUIRE_HERMES", "1"),
    ("OMNIX_LIVE_AGENT_TIMEOUT_SECONDS", "6"),
    ("OMNIX_CHARACTER_MODE_ENABLED", "1"),
    # Agent lifecycle diagnostics are opt-in (WP-10.1).
    ("OMNIX_AGENT_DEBUG_LOGS", "0"),
    ("OMNIX_AGENT_LOG_RETENTION_DAYS", "30"),
    ("OMNIX_AGENT_LOG_MAX_FIELD_CHARS", "12000"),
    # Local TP-Link Kasa smart plugs; host and alias are optional with one device.
    ("OMNIX_KASA_ENABLED", "1"),
    ("OMNIX_KASA_DISCOVERY_TARGET", "255.255.255.255"),
    ("OMNIX_KASA_TIMEOUT_SECONDS", "4"),
    ("OMNIX_KASA_DEVICE_HOST", ""),
    ("OMNIX_KASA_DEVICE_ALIAS", ""),
    # The lightweight image service starts; FLUX.2 [klein] stays unloaded until
    # the Image Generation page loads it.
    ("OMNIX_IMAGE_ENABLED", "1"),
    ("OMNIX_START_IMAGE_SERVICE", "1"),
    ("OMNIX_IMAGE_PRELOAD", "0"),
    ("OMNIX_IMAGE_WARMUP", "0"),
    ("OMNIX_IMAGE_REQUIRE_EXPLICIT_LOAD", "1"),
)


def apply_launcher_environment(root: Path, env: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    """Set the launcher's service environment in ``env`` (the process environment by default)."""
    env = environment() if env is None else env
    for name, value in FIXED_ENVIRONMENT:
        env[name] = value
    for name, value in DEFAULT_ENVIRONMENT:
        env.setdefault(name, value)
    env.setdefault("OMNIX_AGENT_LOG_DIR", str(root / "resources" / "logs" / "agent"))
    env.setdefault("OMNIX_BLOB_ROOT", str((root.parent / "omnix-runtime" / "blobs").resolve()))
    if "OMNIX_FFMPEG" not in env:
        found = sorted(glob.glob(str(root / "venv" / "Lib" / "site-packages" / "imageio_ffmpeg" / "binaries" / "ffmpeg*.exe")))
        if found:
            env["OMNIX_FFMPEG"] = found[0]
    image_service = env.get("OMNIX_IMAGE_ENABLED") == "1" and env.get("OMNIX_START_IMAGE_SERVICE") == "1"
    env["OMNIX_IMAGE_URL"] = "http://127.0.0.1:5301" if image_service else ""
    models = root / "resources" / "models" / "tts"
    qwen = models / "Qwen3-TTS-12Hz-0.6B-Base"
    present = (qwen / "config.json").is_file() and (qwen / "preprocessor_config.json").is_file()
    env["OMNIX_TTS_MODELS_DIR"] = str(models)
    env["OMNIX_TTS_MODEL_DIR"] = str(qwen) if present else ""
    env["OMNIX_QWEN3_TTS_MODEL_DIR"] = str(qwen) if present else ""
    # Agent tools installed under .tools/npm-global (setup) are found first.
    tools = root / ".tools" / "npm-global"
    for tool, variable in (("agent-browser", "OMNIX_AGENT_BROWSER_COMMAND"), ("mcporter", "OMNIX_AGENT_MCPORTER_COMMAND")):
        for candidate in (tools / f"{tool}.cmd", tools / "bin" / tool):
            if candidate.is_file():
                env.setdefault(variable, str(candidate))
                if str(candidate.parent) not in env.get("PATH", "").split(os.pathsep):
                    env["PATH"] = str(candidate.parent) + os.pathsep + env.get("PATH", "")
                break
    return env


def launcher_already_running(port: int = 5055) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def interpreter_problems(
    config: LauncherConfig,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for the TTS and STT runtimes.

    A runtime on another Python version is an error. A missing one is a
    warning: Omnix starts without that voice service (a CPU-only host or a
    container has neither).
    """
    errors: list[str] = []
    warnings: list[str] = []
    for role in ("tts", "stt"):
        python = config.python(role)
        if not Path(python).is_file():
            warnings.append(f"the {role.upper()} runtime is missing ({python}); its service will not start. Run setup to install it.")
            continue
        check = run([python, "-c", "import sys; assert sys.version_info[:2] == (3, 11), sys.version"],
                    capture_output=True, text=True, check=False)
        if check.returncode != 0:
            errors.append(f"the {role.upper()} runtime must be Python 3.11: {python}. Run setup again.")
    return errors, warnings


def ensure_postgres_container(
    name: str,
    *,
    attempts: int = 30,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] = print,
) -> str | None:
    """Start an existing PostgreSQL container and wait for it to be healthy; the error, or None."""

    def docker(*args: str) -> subprocess.CompletedProcess:
        return run(["docker", *args], capture_output=True, text=True, check=False)

    say(f"[POSTGRES] Checking Docker and container {name}...")
    try:
        if docker("info").returncode != 0:
            return "Docker Desktop is not running or is not accessible. Start it, wait until it is ready, and try again."
    except FileNotFoundError:
        return "the Docker CLI was not found. Install or repair Docker Desktop before starting Omnix."
    state = docker("inspect", "--format", "{{.State.Running}}", name)
    if state.returncode != 0:
        return f"PostgreSQL container {name} does not exist. Provision it with docker-compose.postgres.yml and operator-owned credentials first."
    if state.stdout.strip().lower() != "true":
        say(f"[POSTGRES] Starting existing container {name}...")
        if docker("start", name).returncode != 0:
            return f"failed to start PostgreSQL container {name}."
    else:
        say("[POSTGRES] Container is already running.")
    say("[POSTGRES] Waiting for the database health check...")
    for _ in range(max(1, attempts)):
        health = docker("inspect", "--format", "{{.State.Health.Status}}", name)
        if health.stdout.strip().lower() == "healthy":
            say("[POSTGRES] Database is healthy.")
            return None
        sleep(2)
    return f"PostgreSQL container {name} did not become healthy."


def banner(env: MutableMapping[str, str]) -> str:
    lines = [
        "========================================",
        "Omnix Launcher Control",
        "========================================",
        f"Dashboard: {env.get('OMNIX_LAUNCHER_URL')}",
        f"Private app button: {env.get('OMNIX_APP_OPEN_URL')}",
        f"API gateway: {env.get('OMNIX_GATEWAY_URL')}",
        f"[LIVE AGENT] Enabled: {env.get('OMNIX_LIVE_AGENT_ENABLED')}; automatic routing: "
        f"{env.get('OMNIX_LIVE_AGENT_AUTO_ROUTE_ENABLED')}; Hermes required: {env.get('OMNIX_LIVE_AGENT_REQUIRE_HERMES')}",
        f"[HERMES] Enabled: {env.get('HERMES_ENABLED')} at {env.get('HERMES_BASE_URL')}; auto-start: {env.get('OMNIX_START_HERMES')}",
        f"[KASA] Enabled: {env.get('OMNIX_KASA_ENABLED')}; discovery target: {env.get('OMNIX_KASA_DISCOVERY_TARGET')}",
        f"[IMAGE] Service: {env.get('OMNIX_IMAGE_URL') or 'disabled'}; the FLUX.2 [klein] model stays unloaded until requested",
        f"[AGENT] Debug logging: {env.get('OMNIX_AGENT_DEBUG_LOGS')} ({env.get('OMNIX_AGENT_LOG_DIR')})",
    ]
    return "\n".join(lines)


__all__ = [
    "DEFAULT_ENVIRONMENT",
    "FIXED_ENVIRONMENT",
    "LAUNCHER_URL",
    "WEB_URL",
    "apply_launcher_environment",
    "banner",
    "ensure_postgres_container",
    "interpreter_problems",
    "launcher_already_running",
]
