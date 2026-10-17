"""``python -m app.composition.launcher start``: the one way to start Omnix (WP-11.4).

In order: refuse a second launcher, check the interpreters, set the service
environment, start the PostgreSQL container when one is named, check the
database (``--check`` stops here), apply migrations, then serve the launcher
dashboard (port 5055). Once the dashboard answers the gateway is started, and
when the gateway is healthy the web app. ``start_all.sh`` and ``start_all.bat``
are thin wrappers around this command; the Windows wrapper also loads the
protected database credential.
"""
from __future__ import annotations

import argparse
import ipaddress
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import httpx

LAUNCHER_PORT = 5055
_HEADERS = {"X-Omnix-Client": "launcher"}
ROOT = Path(__file__).resolve().parents[4]


def _say(message: str, stream: TextIO | None = None) -> None:
    target = stream or sys.stdout
    target.write(message + "\n")
    target.flush()


def _unspecified(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


def _module(*args: str) -> int:
    return subprocess.run([sys.executable, "-m", *args], check=False).returncode


def start_services(
    launcher_url: str,
    gateway_url: str,
    *,
    timeout_seconds: float,
    web_url: str | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Start the gateway through the launcher, then the web app once the gateway is healthy."""
    owned = client is None
    client = client or httpx.Client(timeout=10, headers=_HEADERS)
    deadline = time.monotonic() + timeout_seconds
    gateway_requested = False
    try:
        while time.monotonic() < deadline:
            try:
                if not gateway_requested:
                    client.get(f"{launcher_url}/api/services").raise_for_status()
                    client.post(f"{launcher_url}/api/services/gateway/start").raise_for_status()
                    gateway_requested = True
                if client.get(f"{gateway_url}/api/health", timeout=2).status_code == 200:
                    web = client.post(f"{launcher_url}/api/services/web/start")
                    if web.status_code == 200 and web.json().get("ok") and (
                        web_url is None or client.get(web_url, timeout=2).status_code < 500
                    ):
                        _say("[STARTUP] Omnix gateway and web app are ready.")
                        return True
            except (httpx.HTTPError, ValueError):
                pass
            sleep(1)
        _say("[STARTUP] WARNING: the gateway and web app were not ready before the startup timeout.")
        return False
    finally:
        if owned:
            client.close()


def _fail(message: str) -> int:
    _say(f"ERROR: {message}", sys.stderr)
    return 1


def start(args: argparse.Namespace) -> int:
    from .config import load_launcher_config
    from .startup import (
        LAUNCHER_URL,
        WEB_URL,
        apply_launcher_environment,
        banner,
        ensure_postgres_container,
        interpreter_problems,
        launcher_already_running,
    )

    if sys.version_info[:2] != (3, 11):
        return _fail(f"Omnix requires Python 3.11; this is {sys.version.split()[0]} ({sys.executable}). Run setup.")
    serving = not (args.postgres_only or args.check)
    if serving and launcher_already_running(LAUNCHER_PORT):
        return _fail(f"a launcher is already listening on {LAUNCHER_URL}. Use the existing dashboard or stop it first.")
    if not args.postgres_only:
        errors, warnings = interpreter_problems(load_launcher_config(ROOT))
        for warning in warnings:
            _say(f"WARNING: {warning}")
        if errors:
            return _fail("; ".join(errors))
    env = apply_launcher_environment(ROOT)
    if args.postgres_container:
        attempts = int(env.get("OMNIX_POSTGRES_START_WAIT_ATTEMPTS", "30") or 30)
        error = ensure_postgres_container(args.postgres_container, attempts=attempts, say=_say)
        if error is not None:
            return _fail(error)
    if args.postgres_only:
        return 0
    _say(banner(env))
    _say(f"[APP][PYTHON] {sys.executable}")
    _say("[POSTGRES] Verifying authoritative database connectivity...")
    if _module("app.persistence", "health") != 0:
        return _fail("PostgreSQL connectivity verification failed. Omnix services were not started; see docs/SETUP.md (PostgreSQL).")
    if args.check:
        return 0
    _say("[POSTGRES] Applying pending schema migrations before starting services...")
    if _module("app.persistence", "migrate") != 0:
        return _fail("PostgreSQL migrations failed. Omnix services were not started.")
    if env.get("OMNIX_KASA_ENABLED") == "1":
        try:
            import kasa  # noqa: F401
        except ImportError as exc:
            _say(f"WARNING: python-kasa could not be imported ({exc}). Run setup to install the hash-locked image runtime.")
    from app.runtime.net import bind_host

    host = bind_host()
    # The autostart thread talks to the dashboard locally even when it listens on all addresses.
    loopback = "127.0.0.1" if _unspecified(host) else host
    timeout = args.startup_timeout or float(env.get("OMNIX_GATEWAY_STARTUP_TIMEOUT_SECONDS", "420") or 420)
    threading.Thread(
        target=start_services,
        args=(f"http://{loopback}:{LAUNCHER_PORT}", args.gateway_url),
        kwargs={"timeout_seconds": timeout, "web_url": WEB_URL},
        name="omnix-launcher-autostart",
        daemon=True,
    ).start()
    if env.get("OMNIX_LAUNCHER_OPEN_BROWSER") == "1":
        _say(f"Opening browser: {LAUNCHER_URL}")
        webbrowser.open(LAUNCHER_URL)
    _say("Starting the launcher dashboard; Ctrl+C stops the launcher. Use the dashboard to stop services.")
    import uvicorn

    uvicorn.run("app.composition.launcher.runtime_control_app:app", host=host, port=LAUNCHER_PORT, lifespan="on")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.composition.launcher", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    starting = commands.add_parser("start", help="migrate and run the launcher with the gateway and web app")
    starting.add_argument("--gateway-url", default="http://127.0.0.1:8000")
    starting.add_argument("--startup-timeout", type=float, default=None,
                          help="seconds to wait for the gateway and web app (default: OMNIX_GATEWAY_STARTUP_TIMEOUT_SECONDS or 420)")
    starting.add_argument("--postgres-container", default=None,
                          help="start this existing Docker container and wait until it is healthy")
    mode = starting.add_mutually_exclusive_group()
    mode.add_argument("--postgres-only", action="store_true", help="only start the PostgreSQL container")
    mode.add_argument("--check", action="store_true", help="stop after the interpreter and database checks")
    args = parser.parse_args(argv)
    return start(args)


if __name__ == "__main__":
    raise SystemExit(main())
