"""Supervise one worker and local API replicas using private stdin shutdown pipes."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping


def wait_for_parent_control() -> None:
    from app.runtime.process_control import wait_for_parent_control as wait

    wait()


def replica_ports(worker_port: int, count: int) -> list[int]:
    if not 0 <= count <= 8 or not 1 <= worker_port <= 65535 - count:
        raise ValueError("Gateway requires a valid worker port and zero to eight API replicas")
    return list(range(worker_port + 1, worker_port + count + 1))


def should_start_local_job_worker(env: Mapping[str, str]) -> bool:
    configured = env.get("OMNIX_LOCAL_JOB_WORKER")
    if configured is not None:
        normalized = configured.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError("OMNIX_LOCAL_JOB_WORKER must be a boolean")
    return env.get("OMNIX_ENV", "development").strip().lower() in {"development", "local"}


def start_local_job_worker(cwd: Path, env: Mapping[str, str] | None = None):
    source = os.environ if env is None else env
    if not should_start_local_job_worker(source):
        return None
    child_env = child_environment("job-worker")
    child_env["OMNIX_LOCAL_JOB_WORKER"] = "0"
    child = subprocess.Popen(
        [sys.executable, "-m", "app.composition.worker", "--managed-stdin"],
        cwd=cwd,
        env=child_env,
        stdin=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    print(f"Job worker started: pid={child.pid}", flush=True)
    return child


def ensure_shared_run_token_key(env) -> None:
    """Replicas must agree on the agent run-token key (WP-4.6).

    The gateway entrypoint normally sets it. Without it, give all children of
    this cluster one generated key; the service token never stands in for it.
    """
    if not env.get("OMNIX_RUN_TOKEN_KEY"):
        env["OMNIX_RUN_TOKEN_KEY"] = secrets.token_urlsafe(32)


def child_environment(role: str) -> dict[str, str]:
    if role not in {"worker", "api", "job-worker"}:
        raise ValueError("Invalid gateway role")
    env = dict(os.environ, OMNIX_GATEWAY_BACKGROUND_ROLE=role)
    # A child that dies in native code otherwise exits without a word in the log.
    env.setdefault("PYTHONFAULTHANDLER", "1")
    if role == "job-worker":
        env.pop("OMNIX_GATEWAY_OWNS_BACKGROUND_RUNTIME", None)
        env.pop("OMNIX_GATEWAY_ALLOW_LOCAL_TTS", None)
    if env.get('OMNIX_TTS_URL'):
        env['OMNIX_GATEWAY_TTS_HTTP'] = '1'
    if role == "api":
        env["OMNIX_TTS_STARTUP_WARMUP"] = "0"
    from app.runtime.config import RuntimeConfig
    RuntimeConfig.from_environment(env)
    return env


def _drain_timeout() -> float:
    from app.runtime.drain import drain_seconds

    # Children drain for up to OMNIX_DRAIN_SECONDS, then shut down.
    return float(drain_seconds() + 20)


def stop_children(children, timeout: float | None = None) -> None:
    timeout = _drain_timeout() if timeout is None else timeout
    # EOF also requests shutdown if the supervisor exits unexpectedly. No public
    # administrative HTTP endpoint or persisted credential is needed.
    for child in children:
        if child.stdin is not None:
            try:
                child.stdin.write("stop\n")
                child.stdin.flush()
                child.stdin.close()
            except (OSError, ValueError):
                pass
    for child in children:
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)


def serve_cluster(args, count: int) -> int:
    ports = [args.port, *replica_ports(args.port, count)]
    if args.reload:
        raise ValueError("Reload is incompatible with supervised gateway replicas")
    # Never evict an unrelated listener. The launcher owns the old gateway's
    # stop/restart sequence; replica ports must be available before starting.
    for port in ports:
        with socket.socket() as sock:
            sock.bind((args.host, port))
    children = []
    stopping = threading.Event()
    if args.managed_stdin:
        def watch_control():
            wait_for_parent_control()
            stopping.set()
        threading.Thread(target=watch_control, name='gateway-supervisor-control', daemon=True).start()
    previous = {}
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            previous[signum] = signal.signal(signum, lambda *_: stopping.set())
    runner = Path(__file__).with_name("run_omnix_gateway.py")
    ensure_shared_run_token_key(os.environ)
    try:
        job_worker = start_local_job_worker(runner.parent.parent, os.environ)
        if job_worker is not None:
            children.append(job_worker)
        for index, port in enumerate(ports):
            role = "worker" if index == 0 else "api"
            child = subprocess.Popen(
                [sys.executable, str(runner), "--app", args.app, "--host", args.host,
                 "--port", str(port), "--api-replicas", "0", "--managed-stdin"],
                cwd=runner.parent.parent,
                # A stable name per replica: its own trade log file, across restarts.
                env={**child_environment(role), "OMNIX_LOCAL_JOB_WORKER": "0",
                     "OMNIX_INSTANCE_NAME": f"{role}-{port}"},
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
            children.append(child)
            print(f"Gateway {role} started: pid={child.pid} port={port}", flush=True)
        while not stopping.wait(.5):
            exited = [(child.pid, child.poll()) for child in children if child.poll() is not None]
            if exited:
                raise RuntimeError(f"Gateway cohort process exited: {exited}")
        return 0
    finally:
        stop_children(children)
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def watch_parent_stdin(server) -> None:
    def watch():
        wait_for_parent_control()
        # Same path as SIGTERM: drain, then exit.
        server.handle_exit(signal.SIGTERM, None)

    threading.Thread(target=watch, name="gateway-parent-control", daemon=True).start()
