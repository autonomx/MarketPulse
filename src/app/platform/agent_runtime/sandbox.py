"""Operator commands for the agent sandbox (WP-4.7).

    python -m app.platform.agent_runtime.sandbox build         # build the sandbox image
    python -m app.platform.agent_runtime.sandbox status        # Docker, image, network, relay
    python -m app.platform.agent_runtime.sandbox check-egress  # prove only the relay is reachable

``check-egress`` starts the managed network and relay, then from a container
on the sandbox network tries the relay (must connect) and an outside address
(must fail). It exits 1 if the sandbox could reach anything but the relay.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from .isolation import (
    DEFAULT_SANDBOX_IMAGE,
    RELAY_CONTAINER,
    SANDBOX_NETWORK,
    AgentIsolationError,
    DockerStrongIsolation,
    unsandboxed_runs_allowed,
)
from typing import Any

DOCKERFILE = Path(__file__).resolve().parents[4] / "deploy" / "docker" / "agent-sandbox.Dockerfile"
# A TCP connect probe run inside the sandbox image; prints "open" or "closed".
_PROBE = (
    "const s=require('net').connect({host:process.argv[1],port:+process.argv[2]});"
    "s.setTimeout(4000);s.on('connect',()=>{console.log('open');process.exit(0)});"
    "for(const e of ['timeout','error'])s.on(e,()=>{console.log('closed');process.exit(0)});"
)


def _run(*argv: str, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def build(image: str) -> int:
    docker = shutil.which("docker")
    if not docker:
        sys.stderr.write("Docker is not installed\n")
        return 1
    if not DOCKERFILE.is_file():
        sys.stderr.write(f"{DOCKERFILE} is missing\n")
        return 1
    completed = subprocess.run([docker, "build", "-f", str(DOCKERFILE), "-t", image, str(DOCKERFILE.parent)])
    return completed.returncode


def status() -> dict[str, Any]:
    isolation = DockerStrongIsolation()
    report: dict[str, Any] = {
        "image": isolation.image,
        "network": isolation.network,
        "operator_network": bool(isolation.operator_network),
        "unsandboxed_runs_allowed": unsandboxed_runs_allowed(),
    }
    try:
        isolation.validate()
        report["ready"] = True
    except AgentIsolationError as exc:
        report["ready"] = False
        report["reason"] = str(exc)
    if isolation.docker:
        relay = _run(isolation.docker, "inspect", "--format",
                     '{{.State.Running}} {{index .Config.Labels "omnix.relay.routes"}}', RELAY_CONTAINER, timeout=30)
        report["relay"] = relay.stdout.strip() if relay.returncode == 0 else "absent"
    return report


def probe(docker: str, image: str, network: str, host: str, port: int) -> str:
    completed = _run(
        docker, "run", "--rm", "--network", network, "--read-only", "--cap-drop", "ALL",
        image, "node", "-e", _PROBE, host, str(port), timeout=120,
    )
    return completed.stdout.strip() or f"error: {completed.stderr.strip()[:200]}"


def check_egress(gateway_url: str, outside: str) -> dict[str, Any]:
    isolation = DockerStrongIsolation()
    isolation.validate()
    isolation.ensure_network([gateway_url])
    assert isolation.docker is not None
    outside_host, _, outside_port = outside.rpartition(":")
    relay_port = int(gateway_url.split("://", 1)[1].split("/", 1)[0].rpartition(":")[2] or 80)
    result: dict[str, Any] = {
        "network": isolation.network,
        "relay": probe(isolation.docker, isolation.image, isolation.network, RELAY_CONTAINER, relay_port),
        "outside": probe(isolation.docker, isolation.image, isolation.network, outside_host, int(outside_port)),
    }
    result["egress_blocked"] = result["outside"] == "closed"
    result["relay_reachable"] = result["relay"] == "open"
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent sandbox image, network and egress check.")
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--image", default=DEFAULT_SANDBOX_IMAGE)
    commands.add_parser("status")
    egress = commands.add_parser("check-egress")
    egress.add_argument("--gateway-url", default="http://127.0.0.1:8000/api/agent-runs")
    egress.add_argument("--outside", default="1.1.1.1:443", help="host:port the sandbox must not reach")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "build":
        return build(args.image)
    if args.command == "status":
        sys.stdout.write(json.dumps(status(), indent=2) + "\n")
        return 0
    try:
        result = check_egress(args.gateway_url, args.outside)
    except AgentIsolationError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["egress_blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SANDBOX_NETWORK", "build", "check_egress", "main", "status"]
