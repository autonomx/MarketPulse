"""The desktop backend supervisor (roadmap §4.3, Phase 5)."""
from __future__ import annotations

import json
import os
import socket
import sys
import subprocess
import threading
import time
from pathlib import Path

import pytest

from marketpulse.env import LaunchConfig
from marketpulse.supervise import Supervisor, SupervisorError, keep_newest, port_free, pump_log, rotate_log

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "build" / "stage"
# The staged interpreter and client per platform (Windows, or macOS with python/bin).
STAGED_PYTHON = STAGE / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
PSQL = STAGE / "postgres" / "bin" / ("psql.exe" if sys.platform == "win32" else "psql")
SECRETS = {"MARKETPULSE_DB_OWNER_PASSWORD": "owner-pw", "MARKETPULSE_DB_APP_PASSWORD": "app-pw",
           "MARKETPULSE_NOTIFY_SECRET": "notify"}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def secrets_env(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)


def _config(tmp_path: Path, **ports: int) -> LaunchConfig:
    return LaunchConfig(install_root=STAGE, data_dir=tmp_path / "data", http_port=ports.get("http", _free_port()),
                        pg_port=ports.get("pg", _free_port()), metrics_port=ports.get("metrics", _free_port()),
                        notify_port=ports.get("notify", _free_port()),
                        postgres_bin=STAGE / "postgres" / "bin" if (STAGE / "postgres" / "bin").is_dir() else None)


def test_a_busy_port_is_refused_not_moved(tmp_path, secrets_env):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        assert port_free(busy) is False
        supervisor = Supervisor(_config(tmp_path, http=busy), lambda event: None)

        with pytest.raises(SupervisorError) as refused:
            supervisor.check_ports()

    assert refused.value.step == "ports" and f"http {busy}" in str(refused.value)
    assert port_free(busy) is True


def test_this_data_folders_own_running_cluster_may_hold_the_postgres_port(tmp_path, secrets_env, monkeypatch):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        supervisor = Supervisor(_config(tmp_path, pg=busy), lambda event: None)
        steps = []
        supervisor.emit = steps.append

        monkeypatch.setattr(supervisor, "postgres_running", lambda: False)
        with pytest.raises(SupervisorError):
            supervisor.check_ports()
        monkeypatch.setattr(supervisor, "postgres_running", lambda: True)
        supervisor.check_ports()
        supervisor.start_postgres()

    assert steps == [{"event": "step", "step": "postgres", "message": "Reconnecting to the database"}]


def test_logs_rotate_past_their_size_keeping_the_newest(tmp_path):
    log = tmp_path / "gateway.log"
    for generation in "abcde":
        log.write_text(generation * 20, encoding="utf-8")
        rotate_log(log, max_bytes=10, keep=3)

    assert not log.exists()
    assert [(tmp_path / f"gateway.log.{index}").read_text(encoding="utf-8")[0] for index in (1, 2, 3)] == ["e", "d", "c"]
    assert not (tmp_path / "gateway.log.4").exists()
    small = tmp_path / "postgres.log"
    small.write_text("short", encoding="utf-8")
    rotate_log(small, max_bytes=10)
    assert small.read_text(encoding="utf-8") == "short"


def test_the_gateway_log_pump_rotates_while_the_gateway_runs(tmp_path, monkeypatch):
    import io

    import marketpulse.supervise as supervise

    monkeypatch.setattr(supervise, "LOG_MAX_BYTES", 100)
    # 29-byte lines: every fourth line takes the file past 100 bytes and rotates it.
    lines = b"".join(f"line {index:03d} of the gateway log\n".encode() for index in range(22))

    pump_log(io.BytesIO(lines), tmp_path / "gateway.log")

    rotated = sorted(path.name for path in tmp_path.iterdir())
    assert rotated == ["gateway.log", "gateway.log.1", "gateway.log.2", "gateway.log.3"]
    assert (tmp_path / "gateway.log").read_bytes() == b"line 020 of the gateway log\nline 021 of the gateway log\n"
    assert (tmp_path / "gateway.log.1").read_bytes().startswith(b"line 016 ")
    assert all(path.stat().st_size <= 100 + 40 for path in tmp_path.iterdir())


def test_only_the_newest_snapshots_are_kept(tmp_path):
    for stamp in ("20261001", "20261003", "20261002", "20261005", "20261004"):
        (tmp_path / f"pgdata-{stamp}").mkdir()

    keep_newest(tmp_path, 3)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["pgdata-20261003", "pgdata-20261004", "pgdata-20261005"]


def test_login_codes_are_only_issued_before_the_owner_has_a_password(tmp_path, secrets_env, monkeypatch):
    supervisor = Supervisor(_config(tmp_path), lambda event: None)
    issued = []
    monkeypatch.setattr(supervisor, "_run", lambda step, command, **kwargs: issued.append(command) or "code-123\n")

    monkeypatch.setattr(supervisor, "owner_has_password", lambda: False)
    assert supervisor.login_code() == "code-123"
    monkeypatch.setattr(supervisor, "owner_has_password", lambda: True)
    assert supervisor.login_code() is None
    assert len(issued) == 1


def test_commands_report_results_and_unknown_ones(tmp_path, secrets_env, monkeypatch):
    events = []
    supervisor = Supervisor(_config(tmp_path), events.append)
    monkeypatch.setattr(supervisor, "owner_has_password", lambda: True)

    supervisor.handle({"cmd": "owner-status", "id": 7})
    supervisor.handle({"cmd": "explode", "id": 8})

    assert events == [
        {"event": "result", "id": 7, "cmd": "owner-status", "has_password": True},
        {"event": "result", "id": 8, "cmd": "explode", "error": "unknown_command"},
    ]


def test_the_omnix_import_runs_off_the_command_loop_with_the_source_in_its_environment(tmp_path, secrets_env, monkeypatch):
    events = []
    supervisor = Supervisor(_config(tmp_path), events.append)
    calls = []

    def run(step, command, *, env=None, log):
        calls.append((command, env["MARKETPULSE_IMPORT_SOURCE_URL"]))
        return '{"applied": false, "tables": []}'

    monkeypatch.setattr(supervisor, "_run", run)
    supervisor.handle({"cmd": "import-reference", "id": 9, "source": "postgresql://u:pw@127.0.0.1/omnix"})
    supervisor.handle({"cmd": "import-reference", "id": 10, "source": "http://elsewhere"})
    deadline = time.monotonic() + 10
    while len(events) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    by_id = {event["id"]: event for event in events}
    assert by_id[9]["applied"] is False and by_id[9]["tables"] == []
    assert "postgresql://" in by_id[10]["error"]
    # The URL (with its password) is never on a command line.
    assert calls == [(supervisor._serve_command("import-reference"), "postgresql://u:pw@127.0.0.1/omnix")]


def test_only_one_omnix_import_runs_at_a_time(tmp_path, secrets_env):
    supervisor = Supervisor(_config(tmp_path), lambda event: None)
    supervisor._import_lock.acquire()

    with pytest.raises(RuntimeError, match="already running"):
        supervisor.import_reference("postgresql://u@127.0.0.1/omnix", apply=True)


def test_serve_children_get_the_secrets_and_the_gateway_environment(tmp_path, secrets_env):
    supervisor = Supervisor(_config(tmp_path), lambda event: None)

    env = supervisor._serve_env()
    assert env["MARKETPULSE_DB_OWNER_PASSWORD"] == "owner-pw"
    assert env["OMNIX_FEATURES"] == "trading"
    assert "MARKETPULSE_DB_OWNER_PASSWORD" not in supervisor.env


staged = pytest.mark.skipif(not STAGED_PYTHON.is_file() or not (STAGE / "postgres" / "bin").is_dir(),
                            reason="run `python build/stage.py all` first")


@staged
def test_the_staged_interpreter_writes_bytecode_to_the_data_folder(tmp_path):
    # python311._pth ignores PYTHONPATH and PYTHONHOME; the bytecode cache prefix still applies, so nothing is written
    # into the install folder (where NSIS could not remove long __pycache__ paths).
    result = subprocess.run([str(STAGED_PYTHON), "-c", "import sys; print(sys.pycache_prefix)"],
                            env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                                 "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")},
                            capture_output=True, text=True, check=True)

    assert result.stdout.strip() == str(tmp_path / "pycache")


USER_SCRIPT_CHECK = """
from app.apps.trading.scripts_service import ScriptBars, ScriptRunService

SOURCE = '//@version=5\\nindicator("SMA")\\nplot(ta.sma(close, 3), "SMA")\\n'
closes = tuple(float(value) for value in range(1, 21))
bars = ScriptBars(time=tuple(1_760_000_000_000 + index * 60_000 for index in range(20)), open=closes, high=closes,
                  low=closes, close=closes, volume=(100.0,) * 20, session=("regular",) * 20)
result = ScriptRunService(workers=1).run(SOURCE, bars, symbol="X", timeframe="1")
print("ran" if "plots" in result or "series" in result or result.get("bars") else result)
"""


@staged
def test_a_user_script_runs_in_a_worker_of_the_staged_runtime(tmp_path):
    """Phase 5 gate: scripts_service starts its worker with the bundled interpreter and gets a result."""
    check = tmp_path / "user_script_check.py"
    check.write_text(USER_SCRIPT_CHECK, encoding="utf-8")
    result = subprocess.run([str(STAGED_PYTHON), str(check)], cwd=str(STAGE), capture_output=True,
                            text=True, timeout=120, env={"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                                                        "OMNIX_FEATURES": "trading"})

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().splitlines()[-1] == "ran"


@staged
@pytest.mark.postgres
def test_the_staged_runtime_starts_from_nothing_and_stops_cleanly(tmp_path):
    """Phase 5 gate: initdb, database, migrations and the gateway from an empty folder, then a clean stop."""
    config = _config(tmp_path)
    launch = tmp_path / "launch.json"
    launch.write_text(json.dumps({
        "install_root": str(STAGE), "data_dir": str(config.data_dir), "http_port": config.http_port,
        "pg_port": config.pg_port, "metrics_port": config.metrics_port, "notify_port": config.notify_port,
        "postgres_bin": str(STAGE / "postgres" / "bin"),
    }), encoding="utf-8")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "OMNIX_"))}, **SECRETS}
    process = subprocess.Popen([str(STAGED_PYTHON), "-m", "marketpulse.supervise", "--config", str(launch)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(tmp_path))
    events: list[dict] = []
    done = threading.Event()

    def read():
        for line in process.stdout:  # type: ignore[union-attr]
            events.append(json.loads(line))
            if events[-1]["event"] in {"ready", "error"}:
                done.set()

    threading.Thread(target=read, daemon=True).start()
    try:
        assert done.wait(300), events
        assert events[-1]["event"] == "ready", events
        assert events[-1]["first_run"] is True
        assert [event["step"] for event in events if event["event"] == "step"] == [
            "initdb", "postgres", "database", "migrate", "gateway"]
        # No password: the cluster refuses a connection without one.
        refused = subprocess.run([str(PSQL), "-h", "127.0.0.1", "-p", str(config.pg_port),
                                  "-U", "marketpulse_owner", "-d", "postgres", "-w", "-c", "select 1"],
                                 capture_output=True, text=True, env={k: v for k, v in env.items() if k != "PGPASSWORD"})
        assert refused.returncode != 0 and "password" in refused.stderr
    finally:
        process.stdin.close()  # type: ignore[union-attr]
        process.wait(90)
    assert events[-1]["event"] == "stopped"
    assert not (config.data_dir / "pgdata" / "postmaster.pid").exists()
    time.sleep(0.5)
    assert port_free(config.http_port) and port_free(config.pg_port)


@staged
@pytest.mark.postgres
def test_a_failed_update_migration_is_rolled_back_from_the_snapshot(tmp_path):
    """Phase 9 gate: a migration failure after the pre-update snapshot, then a restore that starts on the old data."""
    config = _config(tmp_path)
    launch = tmp_path / "launch.json"
    launch.write_text(json.dumps({
        "install_root": str(STAGE), "data_dir": str(config.data_dir), "http_port": config.http_port,
        "pg_port": config.pg_port, "metrics_port": config.metrics_port, "notify_port": config.notify_port,
        "postgres_bin": str(STAGE / "postgres" / "bin"),
    }), encoding="utf-8")
    base = {**{k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "OMNIX_"))}, **SECRETS}
    # The tests' supervisor runs from the source tree, where the test hook is active.
    base["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts")))

    def run(extra_env: dict[str, str], *flags: str) -> list[dict]:
        events: list[dict] = []
        process = subprocess.Popen([sys.executable, "-m", "marketpulse.supervise", "--config", str(launch), *flags],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env={**base, **extra_env}, cwd=str(tmp_path))
        for line in process.stdout:  # type: ignore[union-attr]
            events.append(json.loads(line))
            if events[-1]["event"] in {"ready", "error"}:
                break
        process.stdin.close()  # type: ignore[union-attr]
        process.wait(120)
        return events

    first = run({})
    assert first[-1]["event"] == "ready"
    marker = config.data_dir / "pgdata" / "marketpulse-marker"
    marker.write_text("before the update", encoding="utf-8")

    failed = run({"MARKETPULSE_TEST_FAIL_MIGRATION": "1"})
    assert failed[-1]["event"] == "error" and failed[-1]["step"] == "migrate"
    assert "snapshot" in [event.get("step") for event in failed]
    assert len(list((config.data_dir / "snapshots").glob("pgdata-*"))) == 1

    restored = run({}, "--restore-snapshot")
    assert restored[-1]["event"] == "ready", restored
    assert "restore" in [event.get("step") for event in restored]
    assert marker.read_text(encoding="utf-8") == "before the update"
    assert list(config.data_dir.glob("pgdata-failed-*"))
