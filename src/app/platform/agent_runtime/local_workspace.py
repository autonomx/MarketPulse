"""Validated local workspace selection for Chat-created Agent runs."""
from __future__ import annotations

import ipaddress
from pathlib import Path
import platform
import shutil
import subprocess
from urllib.parse import urlparse


class LocalWorkspaceSelectionError(ValueError):
    pass


def validate_local_workspace_root(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise LocalWorkspaceSelectionError("local workspace path is empty")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalWorkspaceSelectionError(
            f"local workspace does not exist: {raw}"
        ) from exc
    if not path.is_dir():
        raise LocalWorkspaceSelectionError(
            f"local workspace is not a directory: {path}"
        )
    # Agents change a selected folder in place, so it must be one the operator
    # allows (WP-4.7).
    from .request_policy import allowed_workspace_root

    try:
        return allowed_workspace_root(str(path))
    except ValueError as exc:
        raise LocalWorkspaceSelectionError(
            f"local workspace {path} is outside the folders agents may change; "
            "add it to OMNIX_AGENT_WORKSPACE_ROOTS"
        ) from exc


def local_workspace_repository_root(value: str) -> str | None:
    workspace = validate_local_workspace_root(value)
    git = shutil.which("git")
    if not git:
        return None
    try:
        completed = subprocess.run(
            [
                git,
                "-c",
                f"safe.directory={workspace}",
                "-C",
                workspace,
                "rev-parse",
                "--show-toplevel",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        root = Path(completed.stdout.strip()).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return str(root) if root.is_dir() else None


def local_request_host_allowed(host: str | None) -> bool:
    value = str(host or "").strip().casefold()
    if value in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def local_request_origin_allowed(origin: str | None) -> bool:
    value = str(origin or "").strip()
    if not value:
        return True
    try:
        host = urlparse(value).hostname
    except ValueError:
        return False
    return local_request_host_allowed(host)


def pick_local_workspace() -> str | None:
    system = platform.system().casefold()
    if system == "windows":
        selected = _pick_windows()
    elif system == "darwin":
        selected = _pick_macos()
    elif system == "linux":
        selected = _pick_linux()
    else:
        raise LocalWorkspaceSelectionError(
            f"local folder picker is unsupported on {platform.system() or 'this platform'}"
        )
    if not selected:
        return None
    return validate_local_workspace_root(selected)


def _run_picker(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LocalWorkspaceSelectionError(
            f"local folder picker failed: {type(exc).__name__}: {exc}"
        ) from exc
    if completed.returncode not in {0, 1}:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise LocalWorkspaceSelectionError(
            f"local folder picker failed with exit code {completed.returncode}"
            + (f": {detail[:500]}" if detail else "")
        )
    selected = (completed.stdout or "").strip().splitlines()
    return selected[-1].strip() if selected else None


def _pick_windows() -> str | None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise LocalWorkspaceSelectionError("PowerShell is required for the Windows folder picker")
    script = (
        "$ErrorActionPreference='Stop';"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='Select an Omnix Agent workspace folder';"
        "$dialog.ShowNewFolderButton=$false;"
        "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Write-Output $dialog.SelectedPath"
        "}"
    )
    return _run_picker([powershell, "-NoProfile", "-STA", "-Command", script])


def _pick_macos() -> str | None:
    osascript = shutil.which("osascript")
    if not osascript:
        raise LocalWorkspaceSelectionError("osascript is required for the macOS folder picker")
    script = (
        'try\n'
        'set selectedFolder to choose folder with prompt "Select an Omnix Agent workspace folder"\n'
        'POSIX path of selectedFolder\n'
        'on error number -128\n'
        'return ""\n'
        'end try'
    )
    return _run_picker([osascript, "-e", script])


def _pick_linux() -> str | None:
    zenity = shutil.which("zenity")
    if not zenity:
        raise LocalWorkspaceSelectionError(
            "zenity is required for the Linux folder picker"
        )
    return _run_picker([
        zenity,
        "--file-selection",
        "--directory",
        "--title=Select an Omnix Agent workspace folder",
    ])
