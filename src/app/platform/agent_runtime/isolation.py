"""OS/process isolation backends for agent workers (WP-4.7).

A Git worktree is change isolation only. A run that can change its workspace
(edit, write, command or test capability) or that asks for strong isolation
runs in the Docker sandbox: a read-only container without capabilities, with
memory, CPU and pid limits, a private home under /tmp, and a network whose only
reachable endpoint is the Omnix broker relay, which forwards only the
agent-runtime routes (broker and model gateway) and refuses the rest of the
gateway. When the sandbox is unavailable such a run fails closed, unless the
operator sets
``OMNIX_AGENT_ALLOW_UNSANDBOXED=true``: then it runs supervised, every command
needs approval, and the override is audited and shown on the run.
"""
from __future__ import annotations

from app.config.env import env_bool, env_str, environment
from app.runtime.net import CONTAINER_ALL_INTERFACES
from app.security.run_tokens import AGENT_RUNTIME_PATTERNS
from app.security.run_tokens import TOKEN_ENVIRONMENT_KEY as RUN_TOKEN_ENVIRONMENT_KEY

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit, urlunsplit

from .contracts import AgentRunSpec


class AgentIsolationError(RuntimeError):
    pass


# Capabilities that change the workspace or run its code.
MUTATING_CAPABILITIES = frozenset({"workspace.edit", "workspace.write", "workspace.command", "workspace.test"})

# The sandbox image built from deploy/docker/agent-sandbox.Dockerfile.
DEFAULT_SANDBOX_IMAGE = "omnix-agent-sandbox:pi-0.85.1"
SANDBOX_NETWORK = "omnix-agent-sandbox"
RELAY_CONTAINER = "omnix-agent-broker-relay"
# Bumped when the relay's behaviour changes, so a running relay of an older
# version (the raw TCP forwarder before v2) is replaced on the next run.
RELAY_VERSION = "v2"
SANDBOX_HOME = "/tmp/home"
_DOCKER_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class IsolationLimits:
    memory: str = "4g"
    cpus: str = "2"
    pids: int = 256
    tmpfs_size: str = "256m"


def run_mutates(spec: AgentRunSpec) -> bool:
    return bool(MUTATING_CAPABILITIES.intersection(spec.capabilities))


def unsandboxed_runs_allowed() -> bool:
    return env_bool("OMNIX_AGENT_ALLOW_UNSANDBOXED", False)


def _popen(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


class LocalSupervisedIsolation:
    name = "supervised_worktree"
    strong = False

    def launch(self, spec: AgentRunSpec, *, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
        return _popen(argv, cwd=cwd, env=env)


def _docker(docker: str, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [docker, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=_DOCKER_TIMEOUT_SECONDS, check=check,
    )


def _relay_routes(urls: list[str]) -> list[tuple[int, str]]:
    """(port, host target) the relay serves for the gateway URLs a run uses."""
    target_host = (env_str("OMNIX_AGENT_SANDBOX_GATEWAY_HOST", "host.docker.internal") or "").strip()
    ports: dict[int, str] = {}
    for url in urls:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ports[port] = f"{target_host}:{port}"
    return sorted(ports.items())


def relay_arguments(routes: list[tuple[int, str]]) -> list[str]:
    """The relay's arguments: its gateway routes and the only paths it forwards."""
    allowed = "^(?:" + "|".join(AGENT_RUNTIME_PATTERNS) + ")$"
    return [*(f"{port}={target}" for port, target in routes), f"--allow={allowed}"]


def relay_signature(routes: list[tuple[int, str]]) -> str:
    return RELAY_VERSION + ";" + ",".join(f"{port}={target}" for port, target in routes)


def _through_relay(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return urlunsplit((parts.scheme, f"{RELAY_CONTAINER}:{port}", parts.path, parts.query, parts.fragment))


class DockerStrongIsolation:
    name = "docker_strong"
    strong = True

    def __init__(
        self,
        *,
        image: str | None = None,
        network: str | None = None,
        limits: IsolationLimits | None = None,
    ) -> None:
        self.docker = shutil.which("docker")
        self.image = image or environment().get("OMNIX_AGENT_DOCKER_IMAGE") or DEFAULT_SANDBOX_IMAGE
        # An operator-managed network replaces the managed one (the operator
        # then guarantees it reaches only the broker).
        self.operator_network = network or environment().get("OMNIX_AGENT_DOCKER_NETWORK")
        self.network = self.operator_network or SANDBOX_NETWORK
        self.limits = limits or IsolationLimits(
            memory=environment().get("OMNIX_AGENT_DOCKER_MEMORY", "4g"),
            cpus=environment().get("OMNIX_AGENT_DOCKER_CPUS", "2"),
            pids=int(environment().get("OMNIX_AGENT_DOCKER_PIDS", "256")),
            tmpfs_size=environment().get("OMNIX_AGENT_DOCKER_TMPFS", "256m"),
        )

    def validate(self) -> None:
        """Docker answers and the sandbox image exists; raise otherwise."""
        if not self.docker:
            raise AgentIsolationError("the agent sandbox needs Docker, which is not installed")
        try:
            if _docker(self.docker, "version", "--format", "{{.Server.Version}}").returncode != 0:
                raise AgentIsolationError("the agent sandbox needs Docker, which is not running")
            if _docker(self.docker, "image", "inspect", self.image).returncode != 0:
                raise AgentIsolationError(
                    f"the agent sandbox image {self.image} is missing; "
                    "build it with `python -m app.platform.agent_runtime.sandbox build`"
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AgentIsolationError(f"the agent sandbox needs Docker, which did not answer: {exc}") from exc

    def ensure_network(self, urls: list[str]) -> None:
        """Create the internal network and its broker relay (idempotent).

        The network is ``--internal``: containers on it reach nothing outside
        it. The relay is the one container on it with a route out, and it
        forwards only the gateway ports the run uses to the gateway host, and
        on them only the agent-runtime routes (broker and model gateway).
        """
        if self.operator_network:
            return
        assert self.docker is not None
        if _docker(self.docker, "network", "inspect", SANDBOX_NETWORK).returncode != 0:
            created = _docker(self.docker, "network", "create", "--internal", SANDBOX_NETWORK)
            if created.returncode != 0 and "already exists" not in created.stderr:
                raise AgentIsolationError(f"could not create the agent sandbox network: {created.stderr.strip()}")
        routes = _relay_routes(urls)
        signature = relay_signature(routes)
        current = _docker(self.docker, "inspect", "--format",
                          '{{index .Config.Labels "omnix.relay.routes"}}|{{.State.Running}}', RELAY_CONTAINER)
        if current.returncode == 0 and current.stdout.strip() == f"{signature}|true":
            return
        _docker(self.docker, "rm", "-f", RELAY_CONTAINER)
        extension_dir = Path(__file__).resolve().parent
        started = _docker(
            self.docker, "run", "-d", "--name", RELAY_CONTAINER,
            "--label", f"omnix.relay.routes={signature}",
            "--restart", "unless-stopped",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--memory", "128m", "--pids-limit", "64",
            "--add-host", "host.docker.internal:host-gateway",
            "--mount", f"type=bind,source={extension_dir},target=/omnix-agent-runtime,readonly",
            self.image, "node", "/omnix-agent-runtime/sandbox_relay.mjs",
            *relay_arguments(routes),
        )
        if started.returncode != 0:
            raise AgentIsolationError(f"could not start the agent sandbox relay: {started.stderr.strip()}")
        connected = _docker(self.docker, "network", "connect", SANDBOX_NETWORK, RELAY_CONTAINER)
        if connected.returncode != 0 and "already exists" not in connected.stderr:
            raise AgentIsolationError(f"could not attach the relay to the sandbox network: {connected.stderr.strip()}")

    def build_command(self, spec: AgentRunSpec, *, argv: list[str], cwd: Path, env: dict[str, str]) -> list[str]:
        extension_dir = Path(__file__).resolve().parent
        rewritten = self._rewrite_pi_argv(argv, extension_dir)
        command = [
            str(self.docker),
            "run",
            "--rm",
            "-i",
            "--name",
            f"omnix-agent-{spec.run_id[:24]}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--network",
            str(self.network),
            "--memory",
            self.limits.memory,
            "--cpus",
            self.limits.cpus,
            "--pids-limit",
            str(self.limits.pids),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={self.limits.tmpfs_size}",
            "--mount",
            f"type=bind,source={cwd},target=/workspace",
            "--mount",
            f"type=bind,source={extension_dir},target=/omnix-agent-runtime,readonly",
            "--workdir",
            "/workspace",
        ]
        getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
        if getuid is not None and getgid is not None:
            # POSIX hosts: files the agent writes in the bind mount stay the host user's.
            command.extend(["--user", f"{getuid()}:{getgid()}"])
        container_env = self._container_env(spec, env, managed_network=not self.operator_network)
        for key, value in sorted(container_env.items()):
            if key == RUN_TOKEN_ENVIRONMENT_KEY:
                # Passed by name: docker reads the value from its own
                # environment, so the token stays out of the process list.
                command.extend(["--env", key])
                continue
            command.extend(["--env", f"{key}={value}"])
        command.extend([str(self.image), *rewritten])
        return command

    def launch(self, spec: AgentRunSpec, *, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
        self.ensure_network([
            env.get("OMNIX_AGENT_BROKER_URL", "http://127.0.0.1:8000/api/agent-runs"),
            env.get("OMNIX_AGENT_MODEL_GATEWAY_URL", "http://127.0.0.1:8000/api/agent-model/v1"),
        ])
        command = self.build_command(spec, argv=argv, cwd=cwd, env=env)
        host_env = {"PATH": environment().get("PATH", ""), "SYSTEMROOT": environment().get("SYSTEMROOT", "")}
        if RUN_TOKEN_ENVIRONMENT_KEY in env:
            host_env[RUN_TOKEN_ENVIRONMENT_KEY] = env[RUN_TOKEN_ENVIRONMENT_KEY]
        return _popen(command, cwd=cwd, env=host_env)

    @staticmethod
    def _rewrite_pi_argv(argv: list[str], extension_dir: Path) -> list[str]:
        result = ["pi"]
        skip_first = True
        previous = ""
        for item in argv:
            if skip_first:
                skip_first = False
                continue
            path = Path(item)
            if previous == "--tools":
                # The container is Linux: Windows hosts name the shell tool powershell.
                item = ",".join(dict.fromkeys("bash" if tool == "powershell" else tool for tool in item.split(",")))
                result.append(item)
            elif path.is_absolute():
                try:
                    relative = path.resolve().relative_to(extension_dir)
                except ValueError:
                    result.append(item)
                else:
                    result.append(f"/omnix-agent-runtime/{relative.as_posix()}")
            else:
                result.append(item)
            previous = item
        return result

    @staticmethod
    def _container_env(
        spec: AgentRunSpec,
        env: dict[str, str],
        *,
        managed_network: bool = False,
    ) -> dict[str, str]:
        explicit = {
            str(key or "").strip()
            for key in spec.execution.allowed_environment_keys
            if str(key or "").strip()
        }
        allowed = {
            key: value
            for key, value in env.items()
            if key.startswith("OMNIX_AGENT_") or key in explicit
        }
        allowed["OMNIX_AGENT_WORKSPACE"] = "/workspace"
        allowed["OMNIX_AGENT_SANDBOXED"] = "1"
        allowed["HOME"] = SANDBOX_HOME
        if managed_network:
            for key in ("OMNIX_AGENT_BROKER_URL", "OMNIX_AGENT_MODEL_GATEWAY_URL"):
                if key in allowed:
                    allowed[key] = _through_relay(allowed[key])
        if environment().get("OMNIX_AGENT_CONTAINER_MODEL_GATEWAY_URL"):
            allowed["OMNIX_AGENT_MODEL_GATEWAY_URL"] = environment()["OMNIX_AGENT_CONTAINER_MODEL_GATEWAY_URL"]
        if environment().get("OMNIX_AGENT_CONTAINER_BROKER_URL"):
            allowed["OMNIX_AGENT_BROKER_URL"] = environment()["OMNIX_AGENT_CONTAINER_BROKER_URL"]
        return allowed


@dataclass(frozen=True, slots=True)
class IsolationPlan:
    """How one run is isolated, decided before its environment is built."""

    isolation: LocalSupervisedIsolation | DockerStrongIsolation
    sandboxed: bool
    # Why a run that needed the sandbox runs without it (operator override).
    unsandboxed_reason: str | None = None

    def launch(self, spec: AgentRunSpec, *, argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
        return self.isolation.launch(spec, argv=argv, cwd=cwd, env=env)


def plan_isolation(spec: AgentRunSpec) -> IsolationPlan:
    policy = spec.workspace.isolation_policy if spec.workspace else "supervised_worktree"
    if policy not in {"docker_strong", "unattended", "supervised_worktree", "immutable_review_snapshot"}:
        raise AgentIsolationError(f"unknown agent isolation policy: {policy}")
    needs_sandbox = policy in {"docker_strong", "unattended"} or run_mutates(spec)
    if not needs_sandbox:
        # Read-only runs (research, review snapshots) may run supervised.
        return IsolationPlan(LocalSupervisedIsolation(), sandboxed=False)
    docker = DockerStrongIsolation()
    try:
        docker.validate()
    except AgentIsolationError as exc:
        if policy == "unattended" or not unsandboxed_runs_allowed():
            raise AgentIsolationError(
                f"{exc}. Agent runs that can change their workspace run in the Docker sandbox "
                "(docs/security/AGENT_SANDBOX.md); to run them unsandboxed instead, with approval "
                "for every command, set OMNIX_AGENT_ALLOW_UNSANDBOXED=true"
            ) from exc
        return IsolationPlan(LocalSupervisedIsolation(), sandboxed=False, unsandboxed_reason=str(exc))
    return IsolationPlan(docker, sandboxed=True)


def isolation_for_spec(spec: AgentRunSpec):
    return plan_isolation(spec).isolation


def launch_agent_process(
    spec: AgentRunSpec,
    *,
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    return plan_isolation(spec).launch(spec, argv=argv, cwd=cwd, env=env)


def start_sandboxed_preview(
    spec: AgentRunSpec,
    *,
    root: Path,
    package: str,
    port: int,
) -> tuple[subprocess.Popen[str], tuple[str, ...]]:
    """Run a workspace's dev server inside the sandbox, reachable on loopback only.

    The preview container sits on the sandbox network (no route out); an ingress
    relay published on ``127.0.0.1:<port>`` forwards to it. Returns the
    attached ``docker run`` process and the containers to remove when done.
    """
    sandbox = DockerStrongIsolation()
    sandbox.validate()
    sandbox.ensure_network([environment().get("OMNIX_AGENT_BROKER_URL", "http://127.0.0.1:8000/api/agent-runs")])
    assert sandbox.docker is not None
    preview = f"omnix-preview-{spec.run_id[:24]}"
    relay = f"omnix-preview-relay-{spec.run_id[:24]}"
    for name in (preview, relay):
        _docker(sandbox.docker, "rm", "-f", name)
    process = _popen(
        [
            sandbox.docker, "run", "--rm", "--name", preview, "--network", sandbox.network,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--memory", "2g", "--pids-limit", "256", "--tmpfs", "/tmp:rw,nosuid,size=256m",
            "--mount", f"type=bind,source={root},target=/workspace", "--workdir", "/workspace",
            "--env", f"HOME={SANDBOX_HOME}",
            sandbox.image, "npm", "--prefix", package, "run", "dev", "--",
            "--host", CONTAINER_ALL_INTERFACES, "--port", str(port), "--strictPort",
        ],
        cwd=root,
        env={"PATH": environment().get("PATH", ""), "SYSTEMROOT": environment().get("SYSTEMROOT", "")},
    )
    extension_dir = Path(__file__).resolve().parent
    started = _docker(
        sandbox.docker, "run", "-d", "--rm", "--name", relay, "-p", f"127.0.0.1:{port}:{port}",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--memory", "128m", "--pids-limit", "64",
        "--mount", f"type=bind,source={extension_dir},target=/omnix-agent-runtime,readonly",
        # Ingress into the preview (dev server and its websocket reload), not
        # a way out of the sandbox: forward raw bytes.
        sandbox.image, "node", "/omnix-agent-runtime/sandbox_relay.mjs", "--tcp", f"{port}={preview}:{port}",
    )
    connected = (
        _docker(sandbox.docker, "network", "connect", sandbox.network, relay) if started.returncode == 0 else started
    )
    if started.returncode != 0 or connected.returncode != 0:
        process.kill()
        for name in (preview, relay):
            _docker(sandbox.docker, "rm", "-f", name)
        raise AgentIsolationError(f"could not start the sandboxed preview: {(started.stderr or connected.stderr).strip()}")
    return process, (preview, relay)


def remove_containers(names: tuple[str, ...]) -> None:
    docker = shutil.which("docker")
    if docker:
        for name in names:
            try:
                _docker(docker, "rm", "-f", name)
            except (OSError, subprocess.TimeoutExpired):
                pass
