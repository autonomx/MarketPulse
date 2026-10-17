"""Safe process-environment construction for local agent tooling."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import ntpath
import os
import re


# Credentials no child process inherits from Omnix, whatever an allowlist
# says (WP-4.6). The Pi process gets its run token explicitly.
NEVER_FORWARDED_ENVIRONMENT_KEYS = frozenset({
    "OMNIX_AGENT_RUN_TOKEN",
    "OMNIX_RUN_TOKEN_KEY",
    "OMNIX_SERVICE_TOKEN",
})

_WINDOWS_ENV_REFERENCE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:$")


def _is_windows_environment(environment: Mapping[str, str]) -> bool:
    if os.name == "nt":
        return True
    windows_keys = {"systemroot", "windir", "systemdrive", "programdata"}
    return any(str(key).casefold() in windows_keys for key in environment)


def _lookup(environment: Mapping[str, str], key: str, *, case_insensitive: bool) -> str:
    value = environment.get(key)
    if value is not None:
        return str(value)
    if case_insensitive:
        expected = key.casefold()
        for candidate, candidate_value in environment.items():
            if str(candidate).casefold() == expected:
                return str(candidate_value)
    return ""


def _set_canonical(environment: dict[str, str], key: str, value: str) -> None:
    for candidate in tuple(environment):
        if candidate.casefold() == key.casefold():
            environment.pop(candidate, None)
    if value:
        environment[key] = value


def _literal(replacement: str) -> Callable[[re.Match[str]], str]:
    """A replacement function, so backslashes in Windows paths are not escapes."""
    return lambda _match: replacement


def _expand_known_windows_references(value: str, known: Mapping[str, str]) -> str:
    expanded = str(value or "").strip()
    for key, replacement in known.items():
        expanded = re.sub(rf"%{re.escape(key)}%", _literal(str(replacement)), expanded, flags=re.IGNORECASE)
    return expanded


def _absolute_windows_path(value: str, known: Mapping[str, str] | None = None) -> str:
    expanded = _expand_known_windows_references(value, known or {})
    if not expanded or _WINDOWS_ENV_REFERENCE.search(expanded) or not ntpath.isabs(expanded):
        return ""
    return ntpath.normpath(expanded)


def normalize_windows_process_environment(
    environment: Mapping[str, str],
    *,
    windows: bool | None = None,
) -> dict[str, str]:
    """Resolve trusted Windows folder variables without shell expansion.

    Windows registry-backed environment values can contain expandable strings.
    Node and browser processes do not consistently expand those strings when a
    parent supplies a minimal environment, so a literal ``%SystemDrive%`` may be
    treated as a path relative to the repository. Only known non-secret folder
    roots are expanded here; unresolved references are removed.
    """

    result = {str(key): str(value) for key, value in environment.items()}
    is_windows = _is_windows_environment(result) if windows is None else bool(windows)
    if not is_windows:
        return result

    drive = _lookup(result, "SYSTEMDRIVE", case_insensitive=True).strip()
    if not _WINDOWS_DRIVE.fullmatch(drive):
        drive = ""

    raw_root = (
        _lookup(result, "SYSTEMROOT", case_insensitive=True)
        or _lookup(result, "WINDIR", case_insensitive=True)
    )
    direct_root = _absolute_windows_path(raw_root)
    if not drive and direct_root:
        drive, _tail = ntpath.splitdrive(direct_root)
    known = {"SYSTEMDRIVE": drive} if drive else {}
    system_root = _absolute_windows_path(raw_root, known)
    if not system_root and drive:
        system_root = ntpath.join(drive + "\\", "Windows")

    if system_root and not drive:
        drive, _tail = ntpath.splitdrive(system_root)
        known["SYSTEMDRIVE"] = drive
    if system_root:
        known["SYSTEMROOT"] = system_root
        known["WINDIR"] = system_root

    user_profile = _absolute_windows_path(
        _lookup(result, "USERPROFILE", case_insensitive=True),
        known,
    )
    if user_profile:
        known["USERPROFILE"] = user_profile

    program_data = _absolute_windows_path(
        _lookup(result, "PROGRAMDATA", case_insensitive=True),
        known,
    )
    if not program_data and drive:
        program_data = ntpath.join(drive + "\\", "ProgramData")

    local_app_data = _absolute_windows_path(
        _lookup(result, "LOCALAPPDATA", case_insensitive=True),
        known,
    )
    if not local_app_data and user_profile:
        local_app_data = ntpath.join(user_profile, "AppData", "Local")
    if local_app_data:
        known["LOCALAPPDATA"] = local_app_data

    app_data = _absolute_windows_path(
        _lookup(result, "APPDATA", case_insensitive=True),
        known,
    )
    if not app_data and user_profile:
        app_data = ntpath.join(user_profile, "AppData", "Roaming")
    if app_data:
        known["APPDATA"] = app_data

    temp = _absolute_windows_path(_lookup(result, "TEMP", case_insensitive=True), known)
    tmp = _absolute_windows_path(_lookup(result, "TMP", case_insensitive=True), known)
    fallback_temp = ntpath.join(local_app_data, "Temp") if local_app_data else ""
    temp = temp or tmp or fallback_temp
    tmp = tmp or temp

    resolved = {
        "SYSTEMDRIVE": drive,
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "PROGRAMDATA": program_data,
        "USERPROFILE": user_profile,
        "LOCALAPPDATA": local_app_data,
        "APPDATA": app_data,
        "TEMP": temp,
        "TMP": tmp,
    }
    for key, value in resolved.items():
        _set_canonical(result, key, value)
    return result


def bounded_process_environment(
    source: Mapping[str, str],
    keys: Iterable[str],
    *,
    overrides: Mapping[str, str] | None = None,
    windows: bool | None = None,
) -> dict[str, str]:
    """Select an allowlisted environment and normalize its Windows paths."""

    is_windows = _is_windows_environment(source) if windows is None else bool(windows)
    environment: dict[str, str] = {}
    for key in keys:
        if str(key).upper() in NEVER_FORWARDED_ENVIRONMENT_KEYS:
            continue
        value = _lookup(source, str(key), case_insensitive=is_windows)
        if value:
            environment[str(key)] = value
    if overrides:
        environment.update({
            str(key): str(value) for key, value in overrides.items()
            if str(key).upper() not in NEVER_FORWARDED_ENVIRONMENT_KEYS
        })
    return normalize_windows_process_environment(environment, windows=is_windows)
