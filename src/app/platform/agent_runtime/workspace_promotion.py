"""Safely promote an accepted isolated workspace into its repository checkout."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess

from .contracts import RunChangeSet
from .workspace import WorkspaceAuthority, WorkspacePolicyError


class WorkspacePromotionError(RuntimeError):
    """A candidate cannot be applied to the repository without risking data loss."""


@dataclass(frozen=True, slots=True)
class WorkspacePromotionResult:
    status: str
    target_head_sha: str
    paths: tuple[str, ...]


def _normalize_patch_path(value: str) -> str:
    path = str(value or "").replace("\\", "/")
    if (
        not path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise WorkspacePromotionError(f"unsafe patch path: {value}")
    return path


def _patch_paths(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in str(patch).splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            pair = shlex.split(line[len("diff --git "):])
        except ValueError as exc:
            raise WorkspacePromotionError("unable to parse authoritative patch paths") from exc
        if len(pair) != 2:
            raise WorkspacePromotionError("authoritative patch has an invalid diff header")
        for value in pair:
            if value == "/dev/null":
                continue
            if value.startswith(("a/", "b/")):
                value = value[2:]
            paths.add(_normalize_patch_path(value))
    if not paths:
        raise WorkspacePromotionError("authoritative patch contains no file paths")
    return paths


def _allowed_paths(change_set: RunChangeSet) -> set[str]:
    values = {_normalize_patch_path(path) for path in change_set.run_owned_paths}
    values.update(_normalize_patch_path(path) for path in change_set.deletions)
    values.update(_normalize_patch_path(path) for path in change_set.mode_changes)
    for rename in change_set.renames:
        values.add(_normalize_patch_path(rename["from"]))
        values.add(_normalize_patch_path(rename["to"]))
    return values


def _candidate_matches(
    source: WorkspaceAuthority,
    target: WorkspaceAuthority,
    paths: set[str],
) -> bool:
    try:
        return all(source.file_digest(path) == target.file_digest(path) for path in paths)
    except WorkspacePolicyError:
        return False


def _git_apply(target: Path, patch: str, *, check: bool, reverse: bool = False) -> subprocess.CompletedProcess[str]:
    argv = [
        "git",
        "-c",
        f"safe.directory={target}",
        "apply",
    ]
    if check:
        argv.append("--check")
    if reverse:
        argv.append("--reverse")
    argv.extend(["--whitespace=nowarn", "-"])
    return subprocess.run(
        argv,
        cwd=target,
        input=patch,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
        shell=False,
    )


def promote_change_set(
    *,
    source_root: str | Path,
    target_root: str | Path,
    change_set: RunChangeSet,
    patch: str,
) -> WorkspacePromotionResult:
    """Apply one accepted RunChangeSet without overwriting unrelated user dirties.

    The target must still be at the candidate's baseline commit, and none of the
    patch paths may already be dirty there. Repeated calls are safe when the
    target already reproduces the candidate workspace, which covers a process
    crash after applying the patch but before the durable completion marker.
    """

    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    if source == target:
        return WorkspacePromotionResult(
            status="already_in_main",
            target_head_sha=WorkspaceAuthority(target).git_head(),
            paths=tuple(sorted(change_set.run_owned_paths)),
        )
    if not source.is_dir() or not target.is_dir():
        raise WorkspacePromotionError("source or target workspace is unavailable")
    if change_set.baseline_conflicts:
        raise WorkspacePromotionError(
            "candidate contains baseline conflicts: " + ",".join(change_set.baseline_conflicts)
        )
    if not str(patch).strip():
        raise WorkspacePromotionError("authoritative patch is empty")

    source_authority = WorkspaceAuthority(source)
    target_authority = WorkspaceAuthority(target)
    patch_paths = _patch_paths(patch)
    unexpected = patch_paths - _allowed_paths(change_set)
    if unexpected:
        raise WorkspacePromotionError(
            "authoritative patch contains paths outside the RunChangeSet: "
            + ",".join(sorted(unexpected))
        )

    if _candidate_matches(source_authority, target_authority, patch_paths):
        return WorkspacePromotionResult(
            status="already_applied",
            target_head_sha=target_authority.git_head(),
            paths=tuple(sorted(change_set.run_owned_paths)),
        )

    try:
        target_head = target_authority.git_head()
        target_dirty = set(target_authority.git_status_paths())
    except WorkspacePolicyError as exc:
        raise WorkspacePromotionError(f"target repository inspection failed: {exc}") from exc
    if target_head != change_set.baseline_head_sha:
        raise WorkspacePromotionError(
            "target repository advanced since the agent started: "
            f"expected {change_set.baseline_head_sha}, got {target_head}"
        )
    conflicts = target_dirty & patch_paths
    if conflicts:
        raise WorkspacePromotionError(
            "target repository has conflicting dirty paths: " + ",".join(sorted(conflicts))
        )

    checked = _git_apply(target, patch, check=True)
    if checked.returncode != 0:
        detail = (checked.stderr or checked.stdout or "git apply check failed").strip()
        raise WorkspacePromotionError(f"target patch check failed: {detail[-1000:]}")
    applied = _git_apply(target, patch, check=False)
    if applied.returncode != 0:
        detail = (applied.stderr or applied.stdout or "git apply failed").strip()
        raise WorkspacePromotionError(f"target patch application failed: {detail[-1000:]}")
    if not _candidate_matches(source_authority, target_authority, patch_paths):
        rollback = _git_apply(target, patch, check=True, reverse=True)
        if rollback.returncode == 0:
            _git_apply(target, patch, check=False, reverse=True)
        raise WorkspacePromotionError("target post-apply state does not match the accepted candidate")
    return WorkspacePromotionResult(
        status="applied",
        target_head_sha=target_authority.git_head(),
        paths=tuple(sorted(change_set.run_owned_paths)),
    )
