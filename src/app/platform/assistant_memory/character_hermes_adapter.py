"""Optional, review-first Hermes compatibility for one explicit Character owner."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.memory_contracts import MemoryRecord, MemoryScopeContext
from .hermes_adapter import default_hermes_memory_dir
from .service import MemoryService

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
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


class CharacterHermesSyncStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    available: bool
    character_id: str
    memory_dir: str
    imported_candidate_ids: list[str] = Field(default_factory=list)
    exported_memory_ids: list[str] = Field(default_factory=list)
    skipped_reasons: list[str] = Field(default_factory=list)


def default_character_hermes_root() -> Path:
    override = (_env_str("OMNIX_CHARACTER_HERMES_MEMORY_DIR") or "").strip()
    return (
        Path(override).expanduser()
        if override
        else default_hermes_memory_dir() / "characters"
    )


def _owner_root(character_id: str, memory_dir: str | Path | None) -> Path:
    if not _SAFE_ID.fullmatch(character_id):
        raise ValueError("character_id is not safe for Hermes storage")
    base = Path(memory_dir) if memory_dir is not None else default_character_hermes_root()
    return base / character_id


def _validate_owner(context: MemoryScopeContext, character_id: str) -> str | None:
    if context.owner_type != "character":
        return "character_owner_required"
    if context.owner_id != character_id:
        return "character_owner_mismatch"
    return None


def _markers(character_id: str) -> tuple[str, str]:
    return (
        f"<!-- OMNIX CHARACTER {character_id} MANAGED MEMORY BEGIN -->",
        f"<!-- OMNIX CHARACTER {character_id} MANAGED MEMORY END -->",
    )


def _candidate_lines(path: Path, character_id: str) -> list[str]:
    if not path.is_file():
        return []
    begin, end = _markers(character_id)
    managed = False
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped == begin:
            managed = True
            continue
        if stripped == end:
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


def import_character_hermes_memory(
    service: MemoryService,
    context: MemoryScopeContext,
    character_id: str,
    *,
    enabled: bool,
    memory_dir: str | Path | None = None,
) -> CharacterHermesSyncStatus:
    root = _owner_root(character_id, memory_dir)
    owner_error = _validate_owner(context, character_id)
    if owner_error:
        return CharacterHermesSyncStatus(
            enabled=enabled,
            available=root.is_dir(),
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=[owner_error],
        )
    if not enabled:
        return CharacterHermesSyncStatus(
            enabled=False,
            available=root.is_dir(),
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=["character_sync_disabled"],
        )
    if not root.is_dir():
        return CharacterHermesSyncStatus(
            enabled=True,
            available=False,
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=["character_hermes_directory_missing"],
        )

    imported: list[str] = []
    path = root / "CHARACTER.md"
    for content in _candidate_lines(path, character_id):
        digest = hashlib.sha256(
            f"{character_id}\nCHARACTER.md\n{content}".encode("utf-8")
        ).hexdigest()
        candidate = service.propose_memory(
            context,
            source_session_id=context.session_id,
            source_message_id=f"character-hermes:{character_id}:{digest}",
            scope="global",
            category="relationship",
            content=content,
            confidence=0.7,
            source="hermes",
            extraction_metadata={
                "adapter": "character_hermes_file_v1",
                "character_id": character_id,
                "filename": "CHARACTER.md",
                "content_sha256": digest,
                "review_required": True,
            },
        )
        imported.append(candidate.id)
    return CharacterHermesSyncStatus(
        enabled=True,
        available=True,
        character_id=character_id,
        memory_dir=str(root),
        imported_candidate_ids=list(dict.fromkeys(imported)),
    )


def _compatible_records(
    records: list[MemoryRecord],
    character_id: str,
) -> list[MemoryRecord]:
    compatible = [
        record
        for record in records
        if (record.owner_type, record.owner_id) == ("character", character_id)
        and record.status == "active"
        and record.trust_level == "user_approved"
        and record.source != "hermes"
        and record.sensitivity == "normal"
        and record.scope in {"global", "workspace", "project"}
        and record.category in {"preference", "fact", "project", "relationship", "instruction"}
    ]
    return sorted(
        compatible,
        key=lambda record: (
            record.scope,
            record.category,
            record.content.casefold(),
            record.id,
        ),
    )


def _replace_managed_block(
    path: Path,
    character_id: str,
    records: list[MemoryRecord],
) -> None:
    begin, end = _markers(character_id)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    before, marker, rest = existing.partition(begin)
    if marker:
        _, end_marker, after = rest.partition(end)
        if not end_marker:
            after = ""
    else:
        before = existing.rstrip()
        after = ""
    lines = [begin, *(f"- {record.content}" for record in records), end]
    sections = [before.rstrip(), "\n".join(lines), after.lstrip()]
    rendered = "\n\n".join(section for section in sections if section).rstrip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def export_character_memory_to_hermes(
    service: MemoryService,
    context: MemoryScopeContext,
    character_id: str,
    *,
    enabled: bool,
    memory_dir: str | Path | None = None,
) -> CharacterHermesSyncStatus:
    root = _owner_root(character_id, memory_dir)
    owner_error = _validate_owner(context, character_id)
    if owner_error:
        return CharacterHermesSyncStatus(
            enabled=enabled,
            available=root.is_dir(),
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=[owner_error],
        )
    if not enabled:
        return CharacterHermesSyncStatus(
            enabled=False,
            available=root.is_dir(),
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=["character_sync_disabled"],
        )
    try:
        records = _compatible_records(service.list_active(context), character_id)
        _replace_managed_block(root / "CHARACTER.md", character_id, records)
    except OSError as exc:
        return CharacterHermesSyncStatus(
            enabled=True,
            available=False,
            character_id=character_id,
            memory_dir=str(root),
            skipped_reasons=[f"character_hermes_write_failed:{exc}"],
        )
    return CharacterHermesSyncStatus(
        enabled=True,
        available=True,
        character_id=character_id,
        memory_dir=str(root),
        exported_memory_ids=[record.id for record in records],
    )


__all__ = [
    "CharacterHermesSyncStatus",
    "default_character_hermes_root",
    "export_character_memory_to_hermes",
    "import_character_hermes_memory",
]
