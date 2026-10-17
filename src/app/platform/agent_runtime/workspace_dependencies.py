"""Deterministic project dependency preparation for isolated agent worktrees."""
from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
from pathlib import Path
import shutil
import subprocess

from app.runtime.process_environment import normalize_windows_process_environment


class WorkspaceDependencyError(RuntimeError):
    """Raised when a project dependency tree cannot be prepared safely."""


def _dependency_install_timeout() -> int:
    raw = str(environment().get("OMNIX_AGENT_DEPENDENCY_INSTALL_TIMEOUT_SECONDS", "900") or "900").strip()
    try:
        return max(30, min(int(raw), 1800))
    except ValueError:
        return 900


def _auto_install_enabled() -> bool:
    value = str(environment().get("OMNIX_AGENT_AUTO_INSTALL_DEPENDENCIES", "true") or "true").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def _workspace_package_roots(root: Path) -> list[Path]:
    package_json = root / "package.json"
    if not package_json.is_file():
        return []
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    workspaces = payload.get("workspaces") if isinstance(payload, dict) else None
    if not isinstance(workspaces, list):
        return []
    return [
        root / workspace
        for workspace in workspaces
        if isinstance(workspace, str) and not any(marker in workspace for marker in "*?[")
    ]


def _node_modules_ready(
    root: Path,
    required_packages: set[str] | None = None,
    *,
    root_only: bool = False,
) -> bool:
    node_modules = root / "node_modules"
    if not node_modules.is_dir():
        return False
    package_roots = [] if root_only else _workspace_package_roots(root)
    for package in required_packages or set():
        candidates = [node_modules / package / "package.json"]
        candidates.extend(
            package_root / "node_modules" / package / "package.json"
            for package_root in package_roots
        )
        if not any(candidate.is_file() for candidate in candidates):
            return False
    return True


def _node_manifest(root: Path) -> tuple[str, set[str]] | None:
    package_json = root / "package.json"
    if not package_json.is_file():
        return None
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceDependencyError(f"invalid Node package manifest: {package_json}") from exc
    if not isinstance(payload, dict):
        raise WorkspaceDependencyError(f"Node package manifest is not an object: {package_json}")
    project_roots = [root]
    workspaces = payload.get("workspaces")
    if isinstance(workspaces, list):
        for workspace in workspaces:
            if isinstance(workspace, str) and not any(marker in workspace for marker in "*?["):
                candidate = (root / workspace).resolve()
                if candidate.is_dir() and (candidate / "package.json").is_file():
                    project_roots.append(candidate)
    required_packages: set[str] = set()
    for project_root in project_roots:
        try:
            project_payload = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceDependencyError(f"invalid Node package manifest: {project_root / 'package.json'}") from exc
        if not isinstance(project_payload, dict):
            raise WorkspaceDependencyError(f"Node package manifest is not an object: {project_root / 'package.json'}")
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            values = project_payload.get(section)
            if isinstance(values, dict):
                required_packages.update(str(name) for name in values if str(name).strip())
    return "ci" if (root / "package-lock.json").is_file() else "install", required_packages


def _link_existing_node_modules(source_root: Path, target_root: Path, required_packages: set[str]) -> bool:
    source = source_root / "node_modules"
    target = target_root / "node_modules"
    # Only the root dependency tree is linked. A package installed beneath a
    # workspace cannot be reached through that link from the isolated
    # worktree, so fall back to a lockfile install in that case.
    if not _node_modules_ready(source_root, required_packages, root_only=True) or target.exists():
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, target, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            return False
        junction_command = f'mklink /J "{target}" "{source}"'
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", junction_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            return False
    if _node_modules_ready(target_root, required_packages, root_only=True):
        return True
    try:
        if target.is_symlink():
            target.unlink()
        else:
            target.rmdir()
    except OSError as exc:
        raise WorkspaceDependencyError(
            "existing repository node_modules could not be linked with the required packages"
        ) from exc
    return False


def prepare_project_dependencies(*, repository: str | Path, worktree: str | Path) -> str:
    """Prepare lockfile-backed Node dependencies for a newly isolated worktree.

    The repository's existing dependency tree is linked into the worktree when
    possible. Otherwise the worktree installs from its own manifest. Lifecycle
    scripts are disabled because this is setup performed by the trusted gateway,
    not an agent-authorized arbitrary command.
    """
    repository_root = Path(repository).expanduser().resolve()
    worktree_root = Path(worktree).expanduser().resolve()
    # The isolated worktree is the source of truth for the code the agent will
    # execute. The main checkout may contain uncommitted manifest changes that
    # are intentionally absent from the worktree.
    manifest = _node_manifest(worktree_root)
    if manifest is None:
        return "not_applicable"
    if not _auto_install_enabled():
        raise WorkspaceDependencyError(
            "project dependencies are not installed and OMNIX_AGENT_AUTO_INSTALL_DEPENDENCIES is disabled; "
            "run npm ci in the isolated worktree or enable automatic dependency installation"
        )

    install_mode, required_packages = manifest
    if _node_modules_ready(worktree_root, required_packages):
        return "already_ready"
    if _link_existing_node_modules(repository_root, worktree_root, required_packages):
        return "linked_existing"

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise WorkspaceDependencyError("Node dependencies are required but npm was not found on PATH")
    command = [npm, install_mode, "--ignore-scripts", "--include=dev", "--no-audit", "--no-fund"]
    if install_mode == "ci":
        command.append("--prefer-offline")
    else:
        command.append("--package-lock=false")
    process_environment = normalize_windows_process_environment(environment())
    process_environment["CI"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=worktree_root,
            env=process_environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_dependency_install_timeout(),
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceDependencyError(
            f"Node dependency installation timed out after {_dependency_install_timeout()} seconds"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "npm returned a non-zero exit code").strip()
        raise WorkspaceDependencyError(
            f"Node dependency installation failed with exit code {completed.returncode}: {detail[-2000:]}"
        )
    if not _node_modules_ready(worktree_root, required_packages):
        raise WorkspaceDependencyError(
            "Node dependency installation reported success but one or more required package manifests are missing"
        )
    return "installed"
