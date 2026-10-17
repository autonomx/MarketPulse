from __future__ import annotations

from app.config.env import env_str

import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List

_TURN_TRACE_ROWS: ContextVar[List[Dict[str, Any]] | None] = ContextVar(
    "RPG_SESSION_TURN_PERF_TRACE_ROWS",
    default=None,
)


def turn_perf_trace_enabled() -> bool:
    return env_str("RPG_TRACE_SESSION_TURN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _rows() -> List[Dict[str, Any]]:
    rows = _TURN_TRACE_ROWS.get()
    if rows is None:
        rows = []
        _TURN_TRACE_ROWS.set(rows)
    return rows


def record_turn_perf_trace(event: str, **fields: Any) -> None:
    if not turn_perf_trace_enabled():
        return
    _rows().append(
        {
            "event": event,
            "time": round(time.perf_counter(), 6),
            **fields,
        }
    )


def record_turn_perf_trace_stack(event: str, **fields: Any) -> None:
    if not turn_perf_trace_enabled():
        return
    _rows().append(
        {
            "event": event,
            "time": round(time.perf_counter(), 6),
            "stack": [line.strip() for line in traceback.format_stack(limit=18)],
            **fields,
        }
    )


@contextmanager
def traced_turn_stage(event: str, **fields: Any) -> Iterator[None]:
    if not turn_perf_trace_enabled():
        yield
        return
    start = time.perf_counter()
    record_turn_perf_trace(f"{event}_enter", **fields)
    try:
        yield
    finally:
        record_turn_perf_trace(
            f"{event}_exit",
            elapsed_seconds=round(time.perf_counter() - start, 3),
            **fields,
        )


def record_elapsed_turn_stage(stage: str, started: float, **fields: Any) -> None:
    record_turn_perf_trace(
        "runtime_core_stage_elapsed",
        stage=stage,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        **fields,
    )