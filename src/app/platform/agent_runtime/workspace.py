"""Local workspace authority for supervised agent execution."""
from __future__ import annotations
from app.config.env import environment as _environment

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import shutil
import subprocess
import tempfile
from typing import Any, cast

from .contracts import AgentEvent
from app.runtime.process_environment import bounded_process_environment


class WorkspacePolicyError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


_SAFE_EXECUTABLES = {
    "git",
    "python",
    "python.exe",
    "pytest",
    "ruff",
    "node",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}
_BLOCKED_ARGUMENT_FRAGMENTS = (
    "rm -rf",
    "remove-item -recurse",
    "format-volume",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "reg delete",
    "git push",
    "git clean -fd",
    "git reset --hard",
)
_SAFE_PROCESS_ENVIRONMENT_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
)

_WINDOWS_CACHE_CONTAMINATION = re.compile(
    r"(?:^|/)%SystemDrive%/$",
    re.IGNORECASE,
)


def _workspace_process_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Return the bounded process environment needed by local developer tools.

    Workspace commands deliberately do not inherit the complete gateway process
    environment because it may contain credentials.  On Windows, however, system
    folder registry values commonly contain expandable strings such as
    ``%SystemDrive%\\ProgramData``.  Dropping ``SYSTEMDRIVE`` causes those values to
    remain literal and some browser/Node dependencies then create a repo-local
    ``%SystemDrive%`` directory.  Preserve only the non-secret OS/process plumbing
    required for correct expansion and tool execution.
    """

    return bounded_process_environment(
        _environment(),
        _SAFE_PROCESS_ENVIRONMENT_KEYS,
        overrides=overrides,
    )


class WorkspaceAuthority:
    def __init__(
        self,
        root: str | Path,
        *,
        allowed_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
        emit: Callable[[AgentEvent], Any] | None = None,
        run_id: str = "workspace",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.allowed_paths = list(allowed_paths or ["**"])
        self.forbidden_paths = list(forbidden_paths or [])
        self.emit = emit
        self.run_id = run_id

    def resolve_path(self, relative: str | Path = ".") -> Path:
        value = Path(relative)
        if value.is_absolute():
            candidate = value.expanduser().resolve()
        else:
            candidate = (self.root / value).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePolicyError("path escapes issued workspace") from exc
        rel = candidate.relative_to(self.root).as_posix() or "."
        if any(candidate.match(pattern) or Path(rel).match(pattern) for pattern in self.forbidden_paths):
            raise WorkspacePolicyError(f"path is forbidden by workspace policy: {rel}")
        if self.allowed_paths and not any(pattern == "**" or Path(rel).match(pattern) for pattern in self.allowed_paths):
            raise WorkspacePolicyError(f"path is outside allowed workspace paths: {rel}")
        return candidate

    def read_text(self, relative: str | Path) -> str:
        path = self.resolve_path(relative)
        return path.read_text(encoding="utf-8")

    def write_text(self, relative: str | Path, content: str) -> None:
        path = self.resolve_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._event("tool.completed", {"capability": "workspace.write", "path": path.relative_to(self.root).as_posix()})

    def edit_text(self, relative: str | Path, old: str, new: str, *, count: int = 1) -> None:
        if not old:
            raise WorkspacePolicyError("edit old text must be non-empty")
        path = self.resolve_path(relative)
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old)
        if occurrences < count:
            raise WorkspacePolicyError("edit target not found enough times")
        path.write_text(content.replace(old, new, count), encoding="utf-8")
        self._event("tool.completed", {"capability": "workspace.edit", "path": path.relative_to(self.root).as_posix()})

    def list_entries(self, relative: str | Path = ".") -> list[str]:
        path = self.resolve_path(relative)
        if not path.is_dir():
            raise WorkspacePolicyError("workspace list target is not a directory")
        return sorted(item.relative_to(self.root).as_posix() for item in path.iterdir())

    def search_text(self, query: str, relative: str | Path = ".") -> list[dict[str, Any]]:
        if not query:
            return []
        base = self.resolve_path(relative)
        files = [base] if base.is_file() else list(base.rglob("*"))
        matches: list[dict[str, Any]] = []
        for path in files:
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for index, line in enumerate(lines, start=1):
                if query in line:
                    matches.append({"path": path.relative_to(self.root).as_posix(), "line": index, "text": line[:500]})
                    if len(matches) >= 500:
                        return matches
        return matches

    def run_command(
        self,
        argv: list[str],
        *,
        timeout_seconds: int = 600,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        normalized = self._validate_command(argv)
        env = _workspace_process_environment(environment)
        self._event("tool.started", {"capability": "workspace.command", "argv": normalized})
        # A Local-folder checkout can be created by a different OS identity
        # than the gateway worker (for example, the UI test sandbox).  Git's
        # ownership check would otherwise reject every status/diff command
        # even though this workspace was explicitly validated and issued to
        # the run.  Scope the trust to this exact workspace and invocation;
        # never mutate the user's global Git configuration.
        execution_argv = normalized
        if Path(normalized[0]).name.casefold() == "git":
            execution_argv = [normalized[0], "-c", f"safe.directory={self.root}", *normalized[1:]]
        completed = subprocess.run(
            execution_argv,
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(timeout_seconds, 3600)),
            check=False,
            shell=False,
        )
        result = CommandResult(tuple(normalized), completed.returncode, completed.stdout[-100_000:], completed.stderr[-100_000:])
        self._event("tool.completed", {"capability": "workspace.command", "argv": normalized, "returncode": result.returncode})
        return result

    def git_status(self) -> str:
        return self.run_command(["git", "status", "--short"]).stdout

    def git_status_entries(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for line in self.git_status().splitlines():
            if len(line) < 4:
                continue
            status = line[:2]
            value = line[3:].strip()
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            normalized = value.replace("\\", "/")
            if normalized:
                entries[normalized] = status
        return entries

    def git_status_paths(self) -> list[str]:
        return sorted(self.git_status_entries())

    def git_head(self) -> str:
        result = self.run_command(["git", "rev-parse", "HEAD"])
        if result.returncode != 0:
            raise WorkspacePolicyError(result.stderr or "git rev-parse HEAD failed")
        return result.stdout.strip()

    def file_digest(self, relative: str) -> str:
        path = self.resolve_path(relative)
        if not path.exists():
            return "__missing__"
        if path.is_dir():
            return "__directory__"
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise WorkspacePolicyError(f"unable to hash workspace path: {relative}") from exc

    def provenance_snapshot(self) -> dict[str, Any]:
        dirty_paths = self.git_status_paths()
        return {
            "head": self.git_head(),
            "dirty_paths": dirty_paths,
            "dirty_digests": {path: self.file_digest(path) for path in dirty_paths},
        }

    def run_owned_paths(
        self,
        baseline_dirty_paths: list[str] | tuple[str, ...] | set[str],
    ) -> list[str]:
        baseline = {str(path).replace("\\", "/") for path in baseline_dirty_paths}
        return sorted(path for path in self.git_status_paths() if path not in baseline)

    def quarantine_generated_windows_cache_contamination(self) -> list[dict[str, str]]:
        """Move a narrowly identified browser cache artifact out of the worktree.

        This only recognizes an untracked literal ``%SystemDrive%`` directory
        whose sole top-level child is the known Windows cache hierarchy. Any
        ambiguity is left untouched so normal provenance and acceptance fail
        closed instead of discarding possible source files.
        """

        quarantined: list[dict[str, str]] = []
        for relative, status in self.git_status_entries().items():
            normalized = relative.replace("\\", "/")
            if status != "??" or not _WINDOWS_CACHE_CONTAMINATION.search(normalized):
                continue
            source = self.resolve_path(normalized.rstrip("/"))
            cache_root = source / "ProgramData" / "Microsoft" / "Windows" / "Caches"
            try:
                top_level = {entry.name for entry in source.iterdir()}
            except OSError:
                continue
            if not source.is_dir() or top_level != {"ProgramData"} or not cache_root.is_dir():
                continue
            quarantine_parent = Path(tempfile.gettempdir()).resolve() / "omnix-agent-quarantine"
            quarantine_parent.mkdir(parents=True, exist_ok=True)
            quarantine = Path(
                tempfile.mkdtemp(prefix="windows-cache-", dir=quarantine_parent)
            ) / source.name
            shutil.move(str(source), str(quarantine))
            record = {
                "path": normalized,
                "quarantine_path": str(quarantine),
                "reason": "literal_systemdrive_windows_cache",
            }
            quarantined.append(record)
            self._event(
                "run.status",
                {"status": "workspace_contamination_quarantined", **record},
            )
        return quarantined

    def baseline_conflicts(self, baseline_dirty_digests: dict[str, str]) -> list[str]:
        conflicts: list[str] = []
        for relative, expected in baseline_dirty_digests.items():
            try:
                current = self.file_digest(relative)
            except WorkspacePolicyError:
                current = "__unreadable__"
            if current != expected:
                conflicts.append(str(relative).replace("\\", "/"))
        return sorted(set(conflicts))

    def git_tracked_diff(self, paths: list[str] | None = None) -> str:
        scoped_paths = [
            str(path).replace(chr(92), "/")
            for path in (paths or [])
            if str(path).strip()
        ]
        if paths is not None and not scoped_paths:
            return ""
        argv = ["git", "diff", "--no-ext-diff", "--find-renames", "--"]
        if paths is not None:
            argv.extend(scoped_paths)
        result = self.run_command(argv)
        if result.returncode != 0:
            raise WorkspacePolicyError(result.stderr or "git diff failed")
        return result.stdout

    def git_diff(self, paths: list[str] | None = None) -> str:
        scoped_paths = [
            str(path).replace(chr(92), "/")
            for path in (paths or [])
            if str(path).strip()
        ]
        if paths is not None and not scoped_paths:
            return ""
        diff = self.git_tracked_diff(paths)
        if paths is None:
            return diff
        entries = self.git_status_entries()
        for relative in scoped_paths:
            if entries.get(relative) == "??":
                diff += self._untracked_file_diff(relative)
        return diff

    def _untracked_file_diff(self, relative: str) -> str:
        path = self.resolve_path(relative)
        header = (
            f"diff --git a/{relative} b/{relative}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{relative}\n"
        )
        if not path.is_file():
            return header + "@@ untracked path @@\n+[untracked non-file path]\n"
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return header + f"Binary files /dev/null and b/{relative} differ\n"
        if len(text) > 100_000:
            return header + "@@ untracked file @@\n+[untracked text file omitted: larger than 100 KB]\n"
        lines = text.splitlines()
        if not lines:
            return header + "@@ -0,0 +1 @@\n+\n"
        body = "\n".join("+" + line for line in lines)
        return header + f"@@ -0,0 +1,{len(lines)} @@\n" + body + "\n"

    @classmethod
    def create_worktree(cls, repository: str | Path, target: str | Path, *, base_ref: str) -> "WorkspaceAuthority":
        repo = Path(repository).expanduser().resolve()
        target_path = Path(target).expanduser().resolve()
        if target_path.exists():
            raise WorkspacePolicyError("worktree target already exists")
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo}",
                "-C",
                str(repo),
                "worktree",
                "add",
                "--detach",
                str(target_path),
                base_ref,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise WorkspacePolicyError(completed.stderr or "failed to create git worktree")
        return cls(target_path)

    @classmethod
    def remove_worktree(cls, repository: str | Path, target: str | Path) -> None:
        """Remove an exact worktree created for a failed isolated run."""
        repo = Path(repository).expanduser().resolve()
        target_path = Path(target).expanduser().resolve()
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={repo}",
                "-C",
                str(repo),
                "worktree",
                "remove",
                "--force",
                str(target_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            shell=False,
        )
        if completed.returncode != 0 and target_path.exists():
            raise WorkspacePolicyError(completed.stderr or "failed to remove isolated worktree")

    def _validate_command(self, argv: list[str]) -> list[str]:
        if not argv or any(not str(part).strip() for part in argv):
            raise WorkspacePolicyError("command argv must contain non-empty arguments")
        normalized = [str(part) for part in argv]
        executable = Path(normalized[0]).name.casefold()
        if executable not in {item.casefold() for item in _SAFE_EXECUTABLES}:
            raise WorkspacePolicyError(f"executable is not allowed: {normalized[0]}")
        joined = " ".join(normalized).casefold()
        if any(fragment in joined for fragment in _BLOCKED_ARGUMENT_FRAGMENTS):
            raise WorkspacePolicyError("command is blocked by workspace policy")
        if executable == "git" and len(normalized) > 1 and normalized[1].casefold() in {"push", "clean"}:
            raise WorkspacePolicyError("remote/destructive git command is broker-controlled")
        return normalized

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.emit is not None:
            self.emit(AgentEvent(run_id=self.run_id, event_type=cast(Any, event_type), payload=payload))
