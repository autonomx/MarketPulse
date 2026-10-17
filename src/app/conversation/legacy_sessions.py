"""Neutral legacy-session compatibility bridge.

No feature or persistence implementation is imported here. Feature composition
must install durable authority callbacks before legacy session access.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")
_lock = RLock()
_load: Callable[[], dict[str, Any]] | None = None
_save: Callable[[dict[str, Any]], None] | None = None
_update: Callable[[Callable[[dict[str, Any]], Any]], tuple[dict[str, Any], Any]] | None = None


def install_legacy_session_callbacks(
    *,
    load_callback: Callable[[], dict[str, Any]],
    save_callback: Callable[[dict[str, Any]], None],
    update_callback: Callable[[Callable[[dict[str, Any]], Any]], tuple[dict[str, Any], Any]] | None = None,
) -> None:
    global _load, _save, _update
    with _lock:
        _load = load_callback
        _save = save_callback
        _update = update_callback


def clear_legacy_session_callbacks() -> None:
    global _load, _save, _update
    with _lock:
        _load = None
        _save = None
        _update = None


def load_sessions() -> dict[str, Any]:
    with _lock:
        loader = _load
        if loader is None:
            raise RuntimeError("durable legacy session authority is not installed")
    return dict(loader() or {})


def write_legacy_session_state(sessions: dict[str, Any]) -> None:
    payload = dict(sessions or {})
    with _lock:
        saver = _save
        if saver is None:
            raise RuntimeError("durable legacy session authority is not installed")
    saver(payload)


def update_sessions(mutator: Callable[[dict[str, Any]], _T]) -> _T:
    with _lock:
        updater = _update
        if updater is None:
            raise RuntimeError("durable legacy session authority is not installed")
        return updater(mutator)[1]
