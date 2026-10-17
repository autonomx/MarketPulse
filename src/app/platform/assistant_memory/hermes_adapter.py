"""Optional, review-first synchronization with Hermes memory files."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .settings import load_memory_runtime_settings
from .service import MemoryService
from typing import Any, cast

_BEGIN = "<!-- OMNIX MANAGED MEMORY BEGIN -->"
_END = "<!-- OMNIX MANAGED MEMORY END -->"
_BLOCKED_MARKERS = {
    "scratchpad",
    "tool output",
    "command output",
    "execution log",
    "system prompt",
    "ignore previous",
    "password",
    "api key",
    "private key",
    "credential",
}


class HermesSyncStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    available: bool
    memory_dir: str
    imported_candidate_ids: list[str] = Field(default_factory=list)
    exported_memory_ids: list[str] = Field(default_factory=list)
    skipped_reasons: list[str] = Field(default_factory=list)


def hermes_memory_sync_enabled() -> bool:
    return load_memory_runtime_settings().hermes_sync_enabled


def default_hermes_memory_dir() -> Path:
    override = (_env_str("OMNIX_HERMES_MEMORY_DIR") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".hermes" / "memories"


def _system_owner_required(context: MemoryScopeContext, root: Path) -> HermesSyncStatus | None:
    if context.owner_type == "system" and context.owner_id == "system-assistant":
        return None
    return HermesSyncStatus(
        enabled=hermes_memory_sync_enabled(),
        available=root.is_dir(),
        memory_dir=str(root),
        skipped_reasons=["system_owner_required"],
    )


def _candidate_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines: list[str] = []
    managed = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped == _BEGIN:
            managed = True
            continue
        if stripped == _END:
            managed = False
            continue
        if managed or not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        content = stripped.removeprefix("-").strip()
        lowered = content.casefold()
        if len(content) < 3 or any(marker in lowered for marker in _BLOCKED_MARKERS):
            continue
        lines.append(content[:4096])
    return list(dict.fromkeys(lines))


def import_hermes_memory(
    service: MemoryService,
    context: MemoryScopeContext,
    *,
    memory_dir: str | Path | None = None,
) -> HermesSyncStatus:
    root = Path(memory_dir) if memory_dir is not None else default_hermes_memory_dir()
    owner_error = _system_owner_required(context, root)
    if owner_error is not None:
        return owner_error
    if not hermes_memory_sync_enabled():
        return HermesSyncStatus(
            enabled=False,
            available=root.is_dir(),
            memory_dir=str(root),
            skipped_reasons=["sync_disabled"],
        )
    if not root.is_dir():
        return HermesSyncStatus(
            enabled=True,
            available=False,
            memory_dir=str(root),
            skipped_reasons=["hermes_memory_directory_missing"],
        )

    imported: list[str] = []
    for filename, scope, category in (
        ("USER.md", "global", "preference"),
        ("MEMORY.md", "project" if context.project_id else "workspace", "project"),
    ):
        path = root / filename
        for content in _candidate_lines(path):
            digest = hashlib.sha256(f"{filename}\n{content}".encode("utf-8")).hexdigest()
            candidate = service.propose_memory(
                context,
                source_session_id=context.session_id,
                source_message_id=f"hermes:{filename}:{digest}",
                scope=cast(Any, scope),
                category=cast(Any, category),
                content=content,
                confidence=0.75,
                source="hermes",
                extraction_metadata={
                    "adapter": "hermes_file_v1",
                    "filename": filename,
                    "content_sha256": digest,
                    "owner_type": "system",
                    "owner_id": "system-assistant",
                },
            )
            imported.append(candidate.id)
    return HermesSyncStatus(
        enabled=True,
        available=True,
        memory_dir=str(root),
        imported_candidate_ids=list(dict.fromkeys(imported)),
    )


def _compatible_export_records(records: list[MemoryRecord]) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
    user_records: list[MemoryRecord] = []
    operational_records: list[MemoryRecord] = []
    for record in records:
        if (record.owner_type, record.owner_id) != ("system", "system-assistant"):
            continue
        if record.status != "active" or record.trust_level != "user_approved":
            continue
        if record.source == "hermes" or record.sensitivity != "normal":
            continue
        if record.scope == "global" and record.category in {"preference", "fact", "relationship"}:
            user_records.append(record)
        elif record.scope in {"workspace", "project"} and record.category in {"instruction", "project", "fact"}:
            operational_records.append(record)
    key = lambda record: (record.scope, record.category, record.content.casefold(), record.id)
    return sorted(user_records, key=key), sorted(operational_records, key=key)


def _replace_managed_block(path: Path, records: list[MemoryRecord]) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    before, marker, rest = existing.partition(_BEGIN)
    if marker:
        _, end_marker, after = rest.partition(_END)
        if not end_marker:
            after = ""
    else:
        before = existing.rstrip()
        after = ""
    lines = [_BEGIN]
    lines.extend(f"- {record.content}" for record in records)
    lines.append(_END)
    sections = [before.rstrip(), "\n".join(lines), after.lstrip()]
    rendered = "\n\n".join(section for section in sections if section).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def export_approved_memory_to_hermes(
    service: MemoryService,
    context: MemoryScopeContext,
    *,
    memory_dir: str | Path | None = None,
) -> HermesSyncStatus:
    root = Path(memory_dir) if memory_dir is not None else default_hermes_memory_dir()
    owner_error = _system_owner_required(context, root)
    if owner_error is not None:
        return owner_error
    if not hermes_memory_sync_enabled():
        return HermesSyncStatus(
            enabled=False,
            available=root.is_dir(),
            memory_dir=str(root),
            skipped_reasons=["sync_disabled"],
        )
    try:
        root.mkdir(parents=True, exist_ok=True)
        user_records, operational_records = _compatible_export_records(service.list_active(context))
        _replace_managed_block(root / "USER.md", user_records)
        _replace_managed_block(root / "MEMORY.md", operational_records)
    except OSError as exc:
        return HermesSyncStatus(
            enabled=True,
            available=False,
            memory_dir=str(root),
            skipped_reasons=[f"hermes_write_failed:{exc}"],
        )
    exported = [record.id for record in [*user_records, *operational_records]]
    return HermesSyncStatus(
        enabled=True,
        available=True,
        memory_dir=str(root),
        exported_memory_ids=exported,
    )
