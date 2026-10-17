from __future__ import annotations

"""Stable entry point for the dependency-aware interday SHADOW replay."""

import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
for import_root in (REPOSITORY_ROOT / "src", REPOSITORY_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from scripts.trade import run_interday_winner_shadow_replay_dependency_v2 as _impl


def main() -> int:
    return _impl.main()


def __getattr__(name: str) -> Any:
    return getattr(_impl, name)


if __name__ == "__main__":
    raise SystemExit(main())
