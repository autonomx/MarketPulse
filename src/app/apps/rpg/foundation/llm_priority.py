"""Cooperative foreground-first scheduling for RPG LLM work."""
from __future__ import annotations

from contextlib import contextmanager
from threading import Condition
from typing import Iterator


class RpgLlmPriorityGate:
    """Prefer foreground work while allowing bounded background concurrency."""

    def __init__(self, *, max_background: int = 4) -> None:
        self._condition = Condition()
        self._foreground_waiters = 0
        self._foreground_active = False
        self._background_active_count = 0
        self._max_background = max(1, min(int(max_background), 4))

    @contextmanager
    def foreground(self) -> Iterator[None]:
        with self._condition:
            self._foreground_waiters += 1
            try:
                while self._foreground_active or self._background_active_count:
                    self._condition.wait()
                self._foreground_active = True
            finally:
                self._foreground_waiters -= 1
        try:
            yield
        finally:
            with self._condition:
                self._foreground_active = False
                self._condition.notify_all()

    @contextmanager
    def background(self) -> Iterator[None]:
        with self._condition:
            while (
                self._foreground_active
                or self._foreground_waiters
                or self._background_active_count >= self._max_background
            ):
                self._condition.wait()
            self._background_active_count += 1
        try:
            yield
        finally:
            with self._condition:
                self._background_active_count -= 1
                self._condition.notify_all()

    def snapshot(self) -> dict[str, int | bool]:
        with self._condition:
            return {
                "foreground_waiters": self._foreground_waiters,
                "foreground_active": self._foreground_active,
                "background_active": self._background_active_count > 0,
                "background_active_count": self._background_active_count,
                "max_background": self._max_background,
            }


RPG_LLM_PRIORITY_GATE = RpgLlmPriorityGate()


@contextmanager
def foreground_rpg_llm_priority() -> Iterator[None]:
    with RPG_LLM_PRIORITY_GATE.foreground():
        yield


@contextmanager
def background_rpg_llm_priority() -> Iterator[None]:
    with RPG_LLM_PRIORITY_GATE.background():
        yield
