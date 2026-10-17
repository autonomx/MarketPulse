"""A script run's limits, apart from the interpreter so the services that set them load none of it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptLimits:
    max_bars: int = 20_000
    # Loop iterations over the whole run, and per bar.
    max_loop_iterations: int = 5_000_000
    max_loop_iterations_per_bar: int = 100_000
    max_seconds: float = 20.0
    # Per kind (labels, lines, boxes); a script's own max_*_count is capped by it.
    max_drawings: int = 500
    max_plots: int = 64
    # Values: one string's length, one array's or map's size, and the array items a run may allocate in all.
    max_string_length: int = 40_000
    max_collection_size: int = 100_000
    max_allocated_items: int = 10_000_000
    max_alerts: int = 1_000
    # Log messages kept per run (the newest), for the editor's console (TVP-11.2).
    max_logs: int = 1_000
