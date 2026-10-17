"""Verified backup and restore rehearsal for legacy persistence sources."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable


def tree_manifest(root: Path) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    if root.is_file():
        content = root.read_bytes()
        manifest[root.name] = {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        return manifest
    if not root.exists():
        return manifest
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        manifest[path.relative_to(root).as_posix()] = {
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    return manifest


def create_backup(sources: Iterable[Path], destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    source_records: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, source in enumerate(sources):
        source = source.resolve()
        if not source.exists():
            source_records.append({"source": str(source), "exists": False})
            continue
        name = source.name or f"source-{index}"
        if name in used_names:
            name = f"{index}-{name}"
        used_names.add(name)
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        source_records.append(
            {
                "source": str(source),
                "backup_name": name,
                "exists": True,
                "kind": "directory" if source.is_dir() else "file",
                "manifest": tree_manifest(target),
            }
        )
    report = {"format_version": "omnix_legacy_backup_v1", "sources": source_records}
    (destination / "backup-manifest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def rehearse_restore(backup_root: Path) -> dict[str, Any]:
    report_path = backup_root / "backup-manifest.json"
    declared = json.loads(report_path.read_text(encoding="utf-8"))
    mismatches: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="omnix-legacy-restore-") as directory:
        restore_root = Path(directory)
        for source in list(declared.get("sources") or []):
            if source.get("exists") is not True:
                continue
            name = str(source["backup_name"])
            backup_path = backup_root / name
            restore_path = restore_root / name
            if backup_path.is_dir():
                shutil.copytree(backup_path, restore_path)
            else:
                shutil.copy2(backup_path, restore_path)
            actual = tree_manifest(restore_path)
            expected = dict(source.get("manifest") or {})
            if actual != expected:
                mismatches.append(
                    {"backup_name": name, "expected": expected, "actual": actual}
                )
            restored.append(
                {
                    "backup_name": name,
                    "files": len(actual),
                    "bytes": sum(int(item["bytes"]) for item in actual.values()),
                }
            )
    return {
        "ok": not mismatches,
        "format_version": "omnix_legacy_restore_rehearsal_v1",
        "backup_root": str(backup_root),
        "restored": restored,
        "mismatches": mismatches,
    }
