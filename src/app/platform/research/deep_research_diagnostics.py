"""Durable JSON-line diagnostics for Deep Research jobs."""
from __future__ import annotations

import logging

from app.config.env import env_str, environment

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.paths import resources_root

logger = logging.getLogger(__name__)

_LOG_LOCK = threading.Lock()


def deep_research_log_path() -> Path:
    override = environment().get("OMNIX_DEEP_RESEARCH_LOG_PATH", "").strip()
    if override:
        path = Path(override)
    else:
        path = resources_root() / "logs" / "deep-research.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def deep_research_log(job_id: str, event: str, **details: Any) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "monotonic_ms": round(time.perf_counter_ns() / 1_000_000, 3),
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
        "job_id": job_id,
        "event": event,
        **details,
    }
    try:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_default)
        with _LOG_LOCK:
            with deep_research_log_path().open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        logger.debug("suppressed error in %s", "deep_research_log", exc_info=True)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)
