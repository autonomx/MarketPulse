"""The desktop app's backend supervisor (roadmap §4.3, Phase 5).

    python -m marketpulse.supervise --config launch.json

The Electron shell starts this one process and keeps its stdin open. It brings the backend up in order, reports each
step as a JSON line on stdout, and takes commands as JSON lines on stdin. When stdin closes (the shell quit or died),
it stops everything.

Startup:
 1. Refuse a busy port (the HTTP port is part of the origin, so it never moves silently).
 2. First run only: ``initdb`` with scram-sha-256 and the owner password, desktop settings in postgresql.conf.
 3. Start PostgreSQL through ``pg_ctl`` (which also runs it correctly from an elevated parent).
 4. First run only: the ``marketpulse`` database and the ``omnix_app`` runtime role (NOBYPASSRLS).
 5. If migrations are pending on an existing database: stop PostgreSQL, snapshot pgdata, start it again.
 6. Release migrations as the owner role.
 7. Start the gateway (``marketpulse.serve serve``) and wait for ``/ready``.
 8. Report ``ready``, with ``first_run`` true while the owner has no password yet.

On Windows the supervisor puts itself in a Job Object that kills every process in it when its last handle closes, so
PostgreSQL, the gateway and the job worker never outlive it, even if it is killed.

Events (stdout):  {"event": "step", "step": ..., "message": ...}  {"event": "ready", ...}
                  {"event": "error", "step": ..., "message": ..., "log": ...}  {"event": "result", "id": ..., ...}
                  {"event": "stopped"}
Commands (stdin): {"cmd": "login-code", "id": 1}   a one-time owner sign-in code, only while first_run
                  {"cmd": "owner-status", "id": 2}
                  {"cmd": "backup", "id": 3}
                  {"cmd": "import-reference", "id": 4, "source": "postgresql://…", "apply": false}
                      Omnix's market reference data (MP-10.1), on a worker thread; a dry run unless apply
                  {"cmd": "import-account-data", "id": 5, "source": "postgresql://…", "apply": false,
                   "source_workspace": "workspace:local"}
                      one Omnix workspace's documents, alerts and paper accounts (MP-10.2), likewise
                  {"cmd": "stop"}
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from marketpulse.env import (
    DB_APP, DB_NAME, DB_OWNER, SECRET_VARIABLES, SYSTEM_ALLOWLIST, LaunchConfig, child_environment, owner_database_url,
)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_KEPT = 3
BACKUPS_KEPT = 7
READY_TIMEOUT_SECONDS = 420.0
GATEWAY_RESTARTS = 3
# A gateway that has run this long since its last restart has its restart count cleared.
GATEWAY_HEALTHY_SECONDS = 600.0
# Logs: rotated past LOG_MAX_BYTES into name.1 … name.LOGS_KEPT (the gateway's while it runs, the rest at start).
LOG_MAX_BYTES = 10 * 2**20
LOGS_KEPT = 3
POSTGRES_SETTINGS = """
# MarketPulse desktop settings
listen_addresses = '127.0.0.1'
port = {port}
timezone = 'UTC'
log_timezone = 'UTC'
max_connections = 100
shared_buffers = 256MB
logging_collector = off
"""


class SupervisorError(RuntimeError):
    def __init__(self, step: str, message: str, log: str | None = None, snapshot: str | None = None) -> None:
        super().__init__(message)
        self.step = step
        self.log = log
        # A failed update migration: the snapshot this start took just before it, the only one the user may restore.
        self.snapshot = snapshot


# Process containment ---------------------------------------------------------------------------------------------


def contain_children() -> object | None:
    """Windows: put this process (and so its future children) in a kill-on-close Job Object; returns its handle."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # extended limits
        return None
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        return None
    return job


# Small helpers ---------------------------------------------------------------------------------------------------


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def keep_newest(folder: Path, count: int) -> None:
    entries = sorted((entry for entry in folder.iterdir()), key=lambda entry: entry.name, reverse=True)
    for stale in entries[count:]:
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink(missing_ok=True)


def rotate_log(path: Path, max_bytes: int | None = None, keep: int = LOGS_KEPT) -> None:
    """Shifts path -> path.1 -> … -> path.<keep> (the oldest is dropped) once path is over max_bytes."""
    limit = LOG_MAX_BYTES if max_bytes is None else max_bytes
    if not path.is_file() or path.stat().st_size <= limit:
        return
    path.with_name(f"{path.name}.{keep}").unlink(missing_ok=True)
    for index in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        if older.exists():
            older.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def pump_log(stream: Any, path: Path) -> None:
    """Copies a child's output into path line by line, rotating it as it grows (the gateway runs for weeks)."""
    output = path.open("ab")
    try:
        for line in iter(stream.readline, b""):
            output.write(line)
            output.flush()
            if output.tell() > LOG_MAX_BYTES:
                output.close()
                rotate_log(path)
                output = path.open("ab")
    finally:
        output.close()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class Supervisor:
    def __init__(self, config: LaunchConfig, emit: Callable[[dict[str, Any]], None], *, python: str = sys.executable) -> None:
        self.config = config
        self.emit = emit
        self.python = python
        self.env = child_environment(config)
        self.env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))
        self.data = config.data_dir
        self.pgdata = self.data / "pgdata"
        self.logs = self.data / "logs"
        self.gateway: subprocess.Popen[bytes] | None = None
        self.stopping = threading.Event()
        self._import_lock = threading.Lock()
        self.first_run = False
        self._lock = threading.Lock()
        self.stopped = threading.Event()

    # Paths and tools

    def _bin(self, name: str) -> str:
        folder = self.config.postgres_bin
        executable = f"{name}.exe" if sys.platform == "win32" else name
        return str(folder / executable) if folder else executable

    def _serve_env(self, *, owner: bool = True) -> dict[str, str]:
        """marketpulse.serve rebuilds the child environment itself, so it gets the secrets the supervisor holds.

        The gateway (``owner=False``) gets neither the owner password nor the owner URL."""
        env = {**self.env, **{name: os.environ[name] for name in SECRET_VARIABLES if os.environ.get(name)}}
        if not owner:
            env.pop("MARKETPULSE_DB_OWNER_PASSWORD", None)
            env.pop("OMNIX_MIGRATION_DATABASE_URL", None)
        return env

    @property
    def pg_env(self) -> dict[str, str]:
        """PostgreSQL's own tools get the system variables only: no database URLs or other secrets."""
        return {name: value for name, value in self.env.items() if name in SYSTEM_ALLOWLIST or name in {"PATH", "Path"}}

    def _step(self, step: str, message: str) -> None:
        self.emit({"event": "step", "step": step, "message": message})

    def _serve_command(self, *args: str) -> list[str]:
        return [self.python, "-m", "marketpulse.serve", *args, "--config", str(self._config_path())]

    def _config_path(self) -> Path:
        return self.data / "launch.json"

    def _run(self, step: str, command: list[str], *, env: dict[str, str] | None = None, log: str) -> str:
        log_path = self.logs / log
        with log_path.open("ab") as output:
            completed = subprocess.run(command, env=env or self.env, stdout=subprocess.PIPE, stderr=output,
                                       stdin=subprocess.DEVNULL, cwd=str(self.data), check=False)
        if completed.returncode != 0:
            raise SupervisorError(step, f"{Path(command[0]).name} exited with {completed.returncode}", _tail(log_path))
        return completed.stdout.decode("utf-8", errors="replace")

    # Steps

    def check_ports(self) -> None:
        ports = {"http": self.config.http_port, "postgres": self.config.pg_port,
                 "metrics": self.config.metrics_port, "notifications": self.config.notify_port}
        # The notification listener is the shell's; the PostgreSQL port may be held by this data folder's own cluster,
        # left running by a supervisor that was killed (start_postgres reconnects to it).
        skip = {"notifications"} | ({"postgres"} if self.postgres_running() else set())
        busy = [f"{name} {port}" for name, port in ports.items() if name not in skip and not port_free(port)]
        if busy:
            raise SupervisorError("ports", "port_busy: " + ", ".join(busy))

    def init_cluster(self) -> None:
        if (self.pgdata / "PG_VERSION").is_file():
            return
        self.first_run = True
        self._step("initdb", "Creating the database")
        self.pgdata.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=self.data, suffix=".pw", encoding="utf-8") as pwfile:
            pwfile.write(os.environ["MARKETPULSE_DB_OWNER_PASSWORD"])
        try:
            self._run("initdb", [self._bin("initdb"), "-D", str(self.pgdata), "-U", DB_OWNER, "--auth=scram-sha-256",
                                 f"--pwfile={pwfile.name}", "-E", "UTF8", "--locale=C"], env=self.pg_env, log="initdb.log")
        finally:
            Path(pwfile.name).unlink(missing_ok=True)
        with (self.pgdata / "postgresql.conf").open("a", encoding="utf-8") as conf:
            conf.write(POSTGRES_SETTINGS.format(port=self.config.pg_port))

    def postgres_running(self) -> bool:
        """Whether this data folder's cluster is already up (e.g. left by a supervisor that was killed on macOS)."""
        if not (self.pgdata / "postmaster.pid").exists():
            return False
        status = subprocess.run([self._bin("pg_ctl"), "-D", str(self.pgdata), "status"], env=self.pg_env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return status.returncode == 0

    def _not_stopping(self, step: str) -> None:
        # A stop that arrives while starting (the shell quit) wins: nothing is started after it.
        if self.stopping.is_set():
            raise SupervisorError(step, "MarketPulse is stopping")

    def start_postgres(self) -> None:
        self._not_stopping("postgres")
        if self.postgres_running():
            self._step("postgres", "Reconnecting to the database")
            return
        self._step("postgres", "Starting the database")
        log = self.logs / "postgres.log"
        command = [self._bin("pg_ctl"), "-D", str(self.pgdata), "-l", str(log), "-w", "-t", "120",
                   "-o", f"-p {self.config.pg_port}", "start"]
        completed = subprocess.run(command, env=self.pg_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, check=False, cwd=str(self.data),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode != 0:
            raise SupervisorError("postgres", "PostgreSQL did not start", _tail(log))

    def stop_postgres(self) -> None:
        if not (self.pgdata / "postmaster.pid").exists():
            return
        subprocess.run([self._bin("pg_ctl"), "-D", str(self.pgdata), "-w", "-t", "60", "stop", "-m", "fast"],
                       env=self.pg_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _connect(self, database: str):
        import psycopg

        return psycopg.connect(host="127.0.0.1", port=self.config.pg_port, user=DB_OWNER, dbname=database,
                               password=os.environ["MARKETPULSE_DB_OWNER_PASSWORD"], autocommit=True,
                               connect_timeout=10)

    def ensure_database(self) -> None:
        with self._connect("postgres") as connection:
            exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
            if not exists:
                self._step("database", "Creating the MarketPulse database")
                connection.execute(f'CREATE DATABASE "{DB_NAME}"')
            role = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (DB_APP,)).fetchone()
            password = os.environ["MARKETPULSE_DB_APP_PASSWORD"]
            from psycopg import sql

            if not role:
                connection.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(DB_APP), sql.Literal(password)))
            else:
                # The password lives in the OS credential store; keep the role in step with it.
                connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(DB_APP), sql.Literal(password)))
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB_NAME), sql.Identifier(DB_APP)))

    def _test_hook(self, name: str) -> bool:
        """A test-only failure switch. Release builds ship no src/tests_marketpulse, so it is never active there."""
        return bool(os.environ.get(name)) and (ROOT / "src" / "tests_marketpulse").is_dir()

    def pending_migrations(self) -> list[str]:
        if self._test_hook("MARKETPULSE_TEST_FAIL_MIGRATION"):
            return ["test-hook"]
        report = json.loads(self._run("migrate", self._serve_command("status"), env=self._serve_env(), log="migrations.log"))
        return list(report.get("pending") or [])

    def snapshot(self) -> Path:
        """A stopped copy of pgdata, taken before migrations change the schema.

        It is copied under a temporary name and renamed when complete, so an interrupted copy is never mistaken for a
        snapshot."""
        self._step("snapshot", "Saving a copy of your data before the update")
        self.stop_postgres()
        folder = self.data / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        for leftover in folder.glob(".partial-*"):
            shutil.rmtree(leftover, ignore_errors=True)
        stamp = _stamp()
        partial = folder / f".partial-pgdata-{stamp}"
        shutil.copytree(self.pgdata, partial, ignore=shutil.ignore_patterns("postmaster.pid", "postmaster.opts"))
        target = folder / f"pgdata-{stamp}"
        partial.rename(target)
        keep_newest(folder, SNAPSHOTS_KEPT)
        self.start_postgres()
        return target

    def restore_snapshot(self, snapshot: Path) -> Path:
        """Puts back the snapshot a failed update took (named by the error), keeping the failed data beside it."""
        folder = (self.data / "snapshots").resolve()
        chosen = snapshot.resolve()
        if chosen.parent != folder or not chosen.name.startswith("pgdata-") or not (chosen / "PG_VERSION").is_file():
            raise SupervisorError("restore", f"{snapshot} is not a snapshot of this data folder")
        self._step("restore", "Restoring your data from before the update")
        self.stop_postgres()
        if self.pgdata.exists():
            self.pgdata.rename(self.data / f"pgdata-failed-{_stamp()}")
        shutil.copytree(chosen, self.pgdata)
        return chosen

    def migrate(self) -> None:
        self._step("migrate", "Updating the database")
        if self._test_hook("MARKETPULSE_TEST_FAIL_MIGRATION"):
            raise SupervisorError("migrate", "a migration failed (test hook)")
        self._run("migrate", self._serve_command("migrate"), env=self._serve_env(), log="migrations.log")

    def start_gateway(self) -> None:
        self._not_stopping("gateway")
        self._step("gateway", "Starting MarketPulse")
        self.gateway = subprocess.Popen(self._serve_command("serve"), env=self._serve_env(owner=False), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(self.data),
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=pump_log, args=(self.gateway.stdout, self.logs / "gateway.log"), name="gateway-log",
                         daemon=True).start()

    def wait_ready(self, timeout: float = READY_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        url = f"{self.config.origin}/ready"
        while time.monotonic() < deadline and not self.stopping.is_set():
            if self.gateway is not None and self.gateway.poll() is not None:
                raise SupervisorError("gateway", f"the gateway exited with {self.gateway.returncode}",
                                      _tail(self.logs / "gateway.log"))
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status == 200 and json.loads(response.read()).get("ready") is True:
                        return
            except (OSError, ValueError):
                pass
            time.sleep(1.0)
        raise SupervisorError("gateway", "the gateway did not become ready", _tail(self.logs / "gateway.log"))

    def owner_has_password(self) -> bool:
        with self._connect(DB_NAME) as connection:
            return connection.execute(
                "SELECT 1 FROM omnix_user_passwords WHERE user_id = 'user:local'").fetchone() is not None

    def login_code(self) -> str | None:
        """A one-time owner sign-in code, only while the owner has no password (first run)."""
        if self.owner_has_password():
            return None
        output = self._run("login-code", self._serve_command("login-code"), env=self._serve_env(), log="supervisor.log")
        return output.strip().splitlines()[-1]

    def import_reference(self, source: str, *, apply: bool) -> dict[str, Any]:
        """Omnix's market reference data (MP-10.1)."""
        return self._import("import-reference", source, apply=apply)

    def import_account_data(self, source: str, *, apply: bool, source_workspace: str = "") -> dict[str, Any]:
        """One Omnix workspace's documents, alerts and paper accounts (MP-10.2)."""
        extra = ["--source-workspace", source_workspace] if source_workspace else []
        return self._import("import-account-data", source, apply=apply, extra=extra)

    def _import(self, command: str, source: str, *, apply: bool, extra: list[str] | None = None) -> dict[str, Any]:
        if not source.startswith(("postgresql://", "postgres://")):
            raise ValueError("the source must be a postgresql:// URL")
        if not self._import_lock.acquire(blocking=False):
            raise RuntimeError("an import is already running")
        try:
            output = self._run("import", self._serve_command(command, *(["--apply"] if apply else []), *(extra or [])),
                               env={**self._serve_env(), "MARKETPULSE_IMPORT_SOURCE_URL": source}, log="import.log")
        finally:
            self._import_lock.release()
        return json.loads(output)

    def backup(self) -> Path:
        folder = self.data / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"marketpulse-{_stamp()}.dump"
        self._run("backup", self._serve_command("backup", str(target)), env=self._serve_env(), log="backups.log")
        keep_newest(folder, BACKUPS_KEPT)
        return target

    # Lifecycle

    def start(self) -> dict[str, Any]:
        self.data.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        for log in self.logs.glob("*.log"):
            # supervisor.log is the shell's to rotate; a log another process holds open is left for next time.
            if log.name != "supervisor.log":
                with contextlib.suppress(OSError):
                    rotate_log(log)
        self.check_ports()
        self.init_cluster()
        self.start_postgres()
        self.ensure_database()
        taken = self.snapshot() if not self.first_run and self.pending_migrations() else None
        try:
            self.migrate()
        except SupervisorError as exc:
            exc.snapshot = str(taken) if taken is not None else None
            raise
        self.start_gateway()
        self.wait_ready()
        ready = {"event": "ready", "origin": self.config.origin, "first_run": not self.owner_has_password()}
        self.emit(ready)
        return ready

    def watch_gateway(self) -> None:
        """Restarts a gateway that exits on its own, up to GATEWAY_RESTARTS times, then reports the error."""
        restarts = 0
        last_restart = time.monotonic()
        while not self.stopping.is_set():
            if restarts and time.monotonic() - last_restart > GATEWAY_HEALTHY_SECONDS:
                # Running well again: an occasional crash weeks later starts a fresh count.
                restarts = 0
            if self.gateway is not None and self.gateway.poll() is not None and not self.stopping.is_set():
                if restarts >= GATEWAY_RESTARTS:
                    self.emit({"event": "error", "step": "gateway", "message": "the gateway keeps stopping",
                               "log": _tail(self.logs / "gateway.log")})
                    return
                restarts += 1
                last_restart = time.monotonic()
                self._step("gateway", f"Restarting MarketPulse ({restarts}/{GATEWAY_RESTARTS})")
                time.sleep(2.0 * restarts)
                try:
                    self.start_gateway()
                    self.wait_ready()
                    self.emit({"event": "ready", "origin": self.config.origin, "first_run": not self.owner_has_password()})
                except SupervisorError as exc:
                    self.emit({"event": "error", "step": exc.step, "message": str(exc), "log": exc.log})
            self.stopping.wait(1.0)

    def stop(self) -> None:
        """Stops the gateway, then PostgreSQL. A second caller waits until the first has finished."""
        with self._lock:
            if self.stopping.is_set():
                self.stopped.wait(timeout=180)
                return
            self.stopping.set()
        try:
            if self.gateway is not None and self.gateway.poll() is None:
                # The gateway (--managed-stdin) drains and exits when its stdin closes.
                with contextlib.suppress(OSError):
                    self.gateway.stdin.close()  # type: ignore[union-attr]
                try:
                    self.gateway.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.gateway.kill()
            self.stop_postgres()
            self.emit({"event": "stopped"})
        finally:
            # Set even if stopping failed, so a second caller (main, after the command reader) never waits it out.
            self.stopped.set()

    def handle(self, command: dict[str, Any]) -> None:
        name = command.get("cmd")
        reply: dict[str, Any] = {"event": "result", "id": command.get("id"), "cmd": name}
        try:
            if name == "login-code":
                reply["code"] = self.login_code()
            elif name == "owner-status":
                reply["has_password"] = self.owner_has_password()
            elif name == "backup":
                reply["path"] = str(self.backup())
            elif name in {"import-reference", "import-account-data"}:
                # Minutes for a large Omnix: off the command loop, so stop and other commands still answer.
                threading.Thread(target=self._import_command, args=(command, reply), name=name, daemon=True).start()
                return
            elif name == "stop":
                self.stop()
                return
            else:
                reply["error"] = "unknown_command"
        except Exception as exc:  # reported to the shell, never fatal for the backend
            reply["error"] = f"{type(exc).__name__}: {exc}"
        self.emit(reply)


    def _import_command(self, command: dict[str, Any], reply: dict[str, Any]) -> None:
        try:
            source, apply = str(command.get("source") or ""), bool(command.get("apply"))
            if command.get("cmd") == "import-account-data":
                reply.update(self.import_account_data(source, apply=apply,
                                                      source_workspace=str(command.get("source_workspace") or "")))
            else:
                reply.update(self.import_reference(source, apply=apply))
        except Exception as exc:  # reported to the shell
            reply["error"] = f"{type(exc).__name__}: {exc}"
        self.emit(reply)


def _emitter() -> Callable[[dict[str, Any]], None]:
    lock = threading.Lock()

    def emit(event: dict[str, Any]) -> None:
        with lock:
            # The shell may be gone (a crash, or a quit that did not wait): stopping carries on without it.
            with contextlib.suppress(OSError, ValueError):
                sys.stdout.write(json.dumps(event) + "\n")
                sys.stdout.flush()

    return emit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m marketpulse.supervise", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--restore-snapshot", type=Path, metavar="SNAPSHOT",
                        help="put back this snapshot (from a failed update's error) and stop: this build cannot use it")
    args = parser.parse_args(argv)
    emit = _emitter()
    job = contain_children()
    config = LaunchConfig.load(args.config)
    supervisor = Supervisor(config, emit)
    config_copy = supervisor._config_path()
    config_copy.parent.mkdir(parents=True, exist_ok=True)
    if args.config.resolve() != config_copy.resolve():
        shutil.copyfile(args.config, config_copy)
    # Commands arrive on stdin from the start. When the shell closes it (quit or crash), or sends stop, the
    # supervisor stops at once, even in the middle of starting (a migration or a snapshot).
    commands: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def read_commands() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except ValueError:
                continue
            if command.get("cmd") == "stop":
                break
            commands.put(command)
        commands.put(None)
        supervisor.stop()

    threading.Thread(target=read_commands, name="commands", daemon=True).start()
    try:
        if args.restore_snapshot is not None:
            supervisor.data.mkdir(parents=True, exist_ok=True)
            supervisor.logs.mkdir(parents=True, exist_ok=True)
            restored = supervisor.restore_snapshot(args.restore_snapshot)
            # The data is now as the previous release left it; this build would migrate it and fail again, so the
            # shell points the user to the previous installer instead of starting (roadmap MP-9.3).
            emit({"event": "restored", "snapshot": str(restored)})
            return 0
        supervisor.start()
    except SupervisorError as exc:
        emit({"event": "error", "step": exc.step, "message": str(exc), "log": exc.log, "snapshot": exc.snapshot})
        supervisor.stop()
        return 1
    except Exception as exc:
        emit({"event": "error", "step": "startup", "message": f"{type(exc).__name__}: {exc}", "log": None})
        supervisor.stop()
        return 1
    threading.Thread(target=supervisor.watch_gateway, name="gateway-watch", daemon=True).start()
    while True:
        command = commands.get()
        if command is None:
            break
        threading.Thread(target=supervisor.handle, args=(command,), daemon=True).start()
    supervisor.stop()
    del job
    return 0


def new_secret() -> str:
    return secrets.token_urlsafe(32)


__all__ = ["Supervisor", "SupervisorError", "contain_children", "keep_newest", "main", "port_free", "owner_database_url"]


if __name__ == "__main__":
    raise SystemExit(main())
