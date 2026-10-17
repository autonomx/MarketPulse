"""Strict, explicit test quarantine support for the enterprise roadmap."""
from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

import pytest

_ALLOWED_CATEGORIES = {"retired_contract", "env_dependency", "real_bug", "flaky", "unknown"}
_REQUIRED = {"reason", "category", "wp", "added"}


def _entries() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("quarantine.toml")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("quarantine", [])
    if not isinstance(entries, list):
        raise RuntimeError("quarantine.toml must contain [[quarantine]] entries")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not _REQUIRED.issubset(entry):
            raise RuntimeError("every quarantine entry needs reason/category/wp/added")
        if entry["category"] not in _ALLOWED_CATEGORIES:
            raise RuntimeError(f"invalid quarantine category: {entry['category']}")
        nodeid = entry.get("nodeid")
        collect_glob = entry.get("collect_glob")
        if bool(nodeid) == bool(collect_glob):
            raise RuntimeError("quarantine entry must define exactly one of nodeid or collect_glob")
        key = ("nodeid", nodeid) if nodeid else ("collect_glob", collect_glob)
        if key in seen:
            raise RuntimeError(f"duplicate quarantine entry: {key[1]}")
        seen.add(key)
    return entries


def collection_globs() -> list[str]:
    """Return pytest globs relative to src/tests."""
    result: list[str] = []
    for entry in _entries():
        value = entry.get("collect_glob")
        if not value:
            continue
        path = str(value).replace("\\", "/")
        if path.startswith("src/tests/"):
            result.append(path[len("src/tests/"):])
        elif path.startswith("src/"):
            result.append("../" + path[len("src/"):])
        else:
            result.append(path)
    return result


def should_ignore_collection(collection_path: Path) -> bool:
    repository_root = Path(__file__).resolve().parents[2]
    try:
        relative = collection_path.resolve().relative_to(repository_root).as_posix()
    except ValueError:
        return False
    return any(
        str(entry.get("collect_glob", "")).replace("\\", "/") == relative
        for entry in _entries()
    )


def apply_item_quarantine(items: list[pytest.Item]) -> None:
    by_nodeid = {str(entry["nodeid"]): entry for entry in _entries() if entry.get("nodeid")}
    for item in items:
        entry = by_nodeid.get(item.nodeid)
        if entry is None:
            continue
        item.add_marker(pytest.mark.xfail(strict=True, reason=str(entry["reason"])))


def quarantine_count() -> int:
    return len(_entries())
