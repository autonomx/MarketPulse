"""Running Omnix Scripts on the server (TVP-11.1).

Scripts run in worker processes (``scripts/worker.py``), never in the server process: a run that overruns its time is
killed with its worker, which is then replaced. Each user has at most ``per_user`` runs at once. Results are cached
by script, inputs and bars, so every chart showing the same script on the same bars shares one run.

``check`` compiles a script in the server process: parsing is bounded (nesting and chain limits) and runs nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import Any

from app.config.env import environment_copy
from .scripts.errors import ScriptError
from .scripts.limits import ScriptLimits

# A run's budget: wall time inside the worker, and how long the server waits for the worker before killing it.
RUN_LIMITS = ScriptLimits(max_seconds=5.0)
# A deep backtest (TVP-11.5): a strategy over all the history a provider serves, with more time.
BACKTEST_LIMITS = ScriptLimits(max_seconds=30.0)
WORKER_GRACE_SECONDS = 5.0
DEFAULT_WORKERS = 2
DEFAULT_RUNS_PER_USER = 2
CACHE_ENTRIES = 64


class ScriptServiceError(Exception):
    """A run that couldn't be made: the script's error (with its line) or the service's (busy, killed)."""

    def __init__(self, kind: str, message: str, line: int = 0, column: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.line = line
        self.column = column

    def payload(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "line": self.line, "column": self.column}


@dataclass(frozen=True)
class ScriptBars:
    """The bars a run reads, as the worker takes them."""

    time: tuple[int, ...]
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[float, ...]
    session: tuple[str, ...]

    def fingerprint(self) -> str:
        """Bars that differ anywhere give a different run; the hash covers every value."""
        return hashlib.sha256(json.dumps(asdict(self), separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ScriptSecurity:
    """A request.security() context's bars (TVP-11.1): its instrument and interval as Omnix names them."""

    symbol: str
    timeframe: str
    bars: ScriptBars


def check_script(source: str) -> dict[str, Any]:
    """Compile a script: its problems with their line (none when it compiles), its declaration and inputs."""
    from .scripts.runtime import compile_script

    try:
        program = compile_script(source)
    except ScriptError as error:
        return {"diagnostics": [{"kind": error.kind, "message": error.message, "line": error.line, "column": error.column}], "declaration": None, "inputs": []}
    from .scripts.worker import jsonable

    return {
        "diagnostics": [],
        "declaration": jsonable(program.declaration),
        "inputs": [jsonable({"title": item.title, "type": item.type, "default": item.default, "options": item.options}) for item in program.inputs],
    }


def _worker_environment() -> dict[str, str]:
    """The server's environment, with the source root that holds ``app`` first on PYTHONPATH.

    The worker runs ``python -m app.apps.trading.scripts.worker``; without this it finds
    ``app`` only when the server's working directory or PYTHONPATH happens to hold it.
    """
    environment = environment_copy()
    source_root = str(Path(__file__).resolve().parents[3])
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(path for path in (source_root, existing) if path)
    return environment


class _Worker:
    """One worker process and a thread reading its answers."""

    def __init__(self, command: Sequence[str]) -> None:
        self.process = subprocess.Popen(
            list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, env=_worker_environment(),
        )
        self.answers: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.answers.put(line)
        self.answers.put(None)

    def ask(self, job: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(job, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.answers.get(timeout=timeout)
        if line is None:
            raise ScriptServiceError("worker", "the script worker stopped")
        return json.loads(line)

    def kill(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass


def default_worker_command() -> list[str]:
    return [sys.executable, "-m", "app.apps.trading.scripts.worker"]


class ScriptRunService:
    def __init__(
        self,
        *,
        workers: int = DEFAULT_WORKERS,
        per_user: int = DEFAULT_RUNS_PER_USER,
        limits: ScriptLimits = RUN_LIMITS,
        command: Callable[[], list[str]] = default_worker_command,
        cache_entries: int = CACHE_ENTRIES,
        grace_seconds: float = WORKER_GRACE_SECONDS,
    ) -> None:
        self.limits = limits
        self.grace_seconds = grace_seconds
        self.per_user = per_user
        self.command = command
        self.cache_entries = cache_entries
        self._idle: queue.Queue[_Worker | None] = queue.Queue()
        for _ in range(max(1, workers)):
            self._idle.put(None)  # started on first use
        self._users: dict[str, threading.BoundedSemaphore] = {}
        self._users_guard = threading.Lock()
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._cache_guard = threading.Lock()
        self.killed_runs = 0

    def _user_slot(self, user_id: str) -> threading.BoundedSemaphore:
        with self._users_guard:
            return self._users.setdefault(user_id, threading.BoundedSemaphore(self.per_user))

    def run(
        self,
        source: str,
        bars: ScriptBars,
        *,
        inputs: dict[str, Any] | None = None,
        symbol: str = "",
        timeframe: str = "",
        user_id: str = "",
        profile: bool = False,
        limits: ScriptLimits | None = None,
        securities: dict[str, ScriptSecurity] | None = None,
    ) -> dict[str, Any]:
        """A run's result (``worker.result_payload``); ScriptServiceError for the script's error or a busy service.

        ``securities``: the bars of the request.security() contexts (``scripts_security.load_script_securities``)."""
        limits = limits or self.limits
        contexts = {
            name: {"symbol": item.symbol, "timeframe": item.timeframe, "bars": asdict(item.bars)} for name, item in (securities or {}).items()
        }
        fingerprints = {name: [item.symbol, item.timeframe, item.bars.fingerprint()] for name, item in (securities or {}).items()}
        key = hashlib.sha256(
            json.dumps(
                [source, inputs or {}, symbol, timeframe, profile, bars.fingerprint(), asdict(limits), fingerprints], sort_keys=True, default=str,
            ).encode()
        ).hexdigest()
        with self._cache_guard:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        slot = self._user_slot(user_id)
        if not slot.acquire(timeout=0.5):
            raise ScriptServiceError("busy", f"at most {self.per_user} scripts run at once for one person; try again")
        try:
            answer = self._dispatch({
                "id": key, "source": source, "bars": asdict(bars), "inputs": inputs or {}, "symbol": symbol,
                "timeframe": timeframe, "limits": asdict(limits), "profile": profile, "securities": contexts,
            }, limits)
        finally:
            slot.release()
        if "error" in answer:
            error = answer["error"]
            raise ScriptServiceError(str(error.get("kind") or "error"), str(error.get("message")), int(error.get("line") or 0), int(error.get("column") or 0))
        result = answer["result"]
        with self._cache_guard:
            self._cache[key] = result
            while len(self._cache) > self.cache_entries:
                self._cache.popitem(last=False)
        return result

    def _dispatch(self, job: dict[str, Any], limits: ScriptLimits | None = None) -> dict[str, Any]:
        limits = limits or self.limits
        try:
            worker = self._idle.get(timeout=self.limits.max_seconds + self.grace_seconds)
        except queue.Empty as exc:
            raise ScriptServiceError("busy", "every script worker is busy; try again") from exc
        try:
            if worker is None or worker.process.poll() is not None:
                worker = _Worker(self.command())
            answer = worker.ask(job, timeout=limits.max_seconds + self.grace_seconds)
        except queue.Empty:
            # Overran: the worker goes, a new one starts on the next job.
            if worker is not None:
                worker.kill()
            worker = None
            self.killed_runs += 1
            raise ScriptServiceError("limit", f"the script ran for more than {limits.max_seconds:g} s and was stopped") from None
        except (OSError, ValueError, ScriptServiceError) as exc:
            if worker is not None:
                worker.kill()
            worker = None
            if isinstance(exc, ScriptServiceError):
                raise
            raise ScriptServiceError("worker", f"the script worker failed: {type(exc).__name__}") from exc
        finally:
            self._idle.put(worker)
        return answer

    def close(self) -> None:
        while True:
            try:
                worker = self._idle.get_nowait()
            except queue.Empty:
                return
            if worker is not None:
                worker.kill()


_service: ScriptRunService | None = None
_service_guard = threading.Lock()


def default_script_service() -> ScriptRunService:
    global _service
    with _service_guard:
        if _service is None:
            _service = ScriptRunService()
        return _service


def bars_for_script(bars: Sequence[Any]) -> ScriptBars:
    """MarketBar rows (or anything with their fields) as the worker takes them."""
    return ScriptBars(
        time=tuple(int(bar.start_time.timestamp() * 1000) for bar in bars),
        open=tuple(float(bar.open) for bar in bars),
        high=tuple(float(bar.high) for bar in bars),
        low=tuple(float(bar.low) for bar in bars),
        close=tuple(float(bar.close) for bar in bars),
        volume=tuple(float(bar.volume) for bar in bars),
        session=tuple(str(getattr(bar, "session", "") or "regular") for bar in bars),
    )


__all__ = [
    "BACKTEST_LIMITS",
    "RUN_LIMITS",
    "ScriptBars",
    "ScriptRunService",
    "ScriptServiceError",
    "bars_for_script",
    "check_script",
    "default_script_service",
]
