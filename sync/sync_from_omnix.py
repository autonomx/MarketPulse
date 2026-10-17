"""Mirror Omnix into MarketPulse (roadmap §4.2).

    python sync/sync_from_omnix.py sync [--omnix PATH] [--to REF]   # mirror a commit, regenerate derived files
    python sync/sync_from_omnix.py check                            # fail on drift from the recorded commit
    python sync/sync_from_omnix.py tests                            # list the mirrored trading test set

Mirrored files are read from the Omnix commit's git objects (never its working
tree), so a sync is deterministic: the same commit gives the same bytes. The
tree copy replaces every mirrored path and removes mirrored files the commit no
longer has. Derived files (pyproject.toml, web/package.json) are generated from
Omnix files at the same commit plus MarketPulse templates. omnix-sync.json
records the commit and a SHA-256 of every mirrored and derived file.

Standard library only: it runs before any environment exists.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "sync" / "omnix-manifest.toml"
LOCK = ROOT / "sync" / "omnix-sync.json"
TRADING_TESTS = ROOT / "sync" / "trading-tests.txt"


class SyncError(RuntimeError):
    pass


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A path glob where ``**`` crosses directories and ``*`` does not."""
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def load_manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def mirrored(path: str, manifest: dict) -> bool:
    rules = manifest["mirror"]
    if not any(_glob_regex(pattern).match(path) for pattern in rules["include"]):
        return False
    return not any(_glob_regex(pattern).match(path) for pattern in rules.get("exclude", []))


def _git(checkout: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(["git", "-C", str(checkout), *args], input=input_bytes, capture_output=True, check=False)
    if completed.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed: {completed.stderr.decode(errors='replace').strip()}")
    return completed.stdout


def resolve_commit(checkout: Path, ref: str) -> str:
    return _git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def tree_files(checkout: Path, commit: str) -> list[str]:
    listing = _git(checkout, "ls-tree", "-r", "-z", "--name-only", commit)
    return [name for name in listing.decode("utf-8").split("\0") if name]


def read_blobs(checkout: Path, commit: str, paths: list[str]) -> dict[str, bytes]:
    """Every path's bytes at ``commit``, read through one ``git cat-file --batch``."""
    if not paths:
        return {}
    request = "".join(f"{commit}:{path}\n" for path in paths).encode("utf-8")
    raw = _git(checkout, "cat-file", "--batch", input_bytes=request)
    blobs: dict[str, bytes] = {}
    offset = 0
    for path in paths:
        header_end = raw.index(b"\n", offset)
        header = raw[offset:header_end].decode()
        if header.endswith(" missing"):
            raise SyncError(f"{path} is missing at {commit}")
        size = int(header.rsplit(" ", 1)[1])
        start = header_end + 1
        blobs[path] = raw[start:start + size]
        offset = start + size + 1
    return blobs


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Derived files ---------------------------------------------------------------------------------------------------


def _toml_tables(text: str, prefixes: list[str]) -> str:
    """The text of every table (and its sub-tables) whose name starts with one of ``prefixes``."""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        header = re.match(r"^\s*\[\[?([^\]]+)\]\]?\s*$", line)
        if header:
            if current is not None:
                blocks.append("\n".join(current).rstrip())
            name = header.group(1).strip()
            keep = any(name == prefix or name.startswith(prefix + ".") or name.startswith(prefix + "_")
                       for prefix in prefixes)
            current = [line] if keep else None
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current).rstrip())
    return "\n\n".join(blocks) + "\n"


def derive_pyproject(manifest: dict, blobs: dict[str, bytes]) -> bytes:
    spec = manifest["derived"]["pyproject"]
    head = (ROOT / spec["head"]).read_text(encoding="utf-8").rstrip() + "\n\n"
    omnix = blobs[spec["from"]].decode("utf-8")
    requires = re.search(r'^requires-python\s*=\s*"([^"]+)"', omnix, re.M)
    if requires is None:
        raise SyncError("Omnix pyproject.toml has no requires-python")
    head = head.replace("{{requires_python}}", requires.group(1))
    note = "# Below: tables copied from Omnix's pyproject.toml by sync/sync_from_omnix.py; do not edit here.\n\n"
    return (head + note + _toml_tables(omnix, spec["tables"])).encode("utf-8")


def _locked_version(lock: dict, name: str) -> str:
    # The web workspace's own copy wins over the hoisted one: npm nests it when versions differ.
    entry = lock.get("packages", {}).get(f"web/node_modules/{name}") or lock.get("packages", {}).get(f"node_modules/{name}")
    if not entry or "version" not in entry:
        raise SyncError(f"{name} is not in Omnix's package-lock.json")
    return str(entry["version"])


def derive_web_package(manifest: dict, blobs: dict[str, bytes]) -> bytes:
    spec = manifest["derived"]["web_package"]
    omnix_web = json.loads(blobs[spec["from"]])
    omnix_root = json.loads(blobs[spec["root"]])
    lock = json.loads(blobs[spec["lock"]])
    package = json.loads((ROOT / spec["template"]).read_text(encoding="utf-8"))
    # Exact versions from Omnix's lock, so MarketPulse runs what Omnix tested.
    package["dependencies"] = {name: _locked_version(lock, name) for name in sorted(omnix_web.get("dependencies", {}))}
    dev = dict(omnix_web.get("devDependencies", {}))
    dev.update(omnix_root.get("devDependencies", {}))
    package["devDependencies"] = {name: _locked_version(lock, name) for name in sorted(dev)}
    return (json.dumps(package, indent=2) + "\n").encode("utf-8")


def derived_files(manifest: dict, blobs: dict[str, bytes]) -> dict[str, bytes]:
    return {
        "pyproject.toml": derive_pyproject(manifest, blobs),
        "web/package.json": derive_web_package(manifest, blobs),
    }


def derived_sources(manifest: dict) -> list[str]:
    spec = manifest["derived"]
    return sorted({spec["pyproject"]["from"], spec["web_package"]["from"], spec["web_package"]["lock"],
                   spec["web_package"]["root"]})


# Commands --------------------------------------------------------------------------------------------------------


def _read_lock() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8")) if LOCK.exists() else {"commit": None, "files": {}, "derived": {}}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def trading_tests(files: list[str]) -> list[str]:
    return sorted(path for path in files if path.startswith("src/tests/") and Path(path).name.startswith("test_")
                  and path.endswith(".py"))


def command_sync(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    checkout = Path(args.omnix or manifest["source"]["checkout"])
    commit = resolve_commit(checkout, args.to or manifest["source"]["ref"])
    previous = _read_lock()
    files = sorted(path for path in tree_files(checkout, commit) if mirrored(path, manifest))
    if not files:
        raise SyncError("the manifest matches no files at that commit")
    blobs = read_blobs(checkout, commit, sorted(set(files) | set(derived_sources(manifest))))
    for stale in sorted(set(previous.get("files", {})) - set(files)):
        target = ROOT / stale
        if target.exists():
            target.unlink()
    hashes = {}
    for path in files:
        _write(ROOT / path, blobs[path])
        hashes[path] = sha256(blobs[path])
    derived = derived_files(manifest, blobs)
    for path, data in derived.items():
        _write(ROOT / path, data)
    # LF on every platform: these files are read by shells and compared byte for byte.
    TRADING_TESTS.write_text("\n".join(trading_tests(files)) + "\n", encoding="utf-8", newline="\n")
    LOCK.write_text(json.dumps({
        "commit": commit,
        "source": manifest["source"]["checkout"],
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": hashes,
        "derived": {path: sha256(data) for path, data in derived.items()},
    }, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    old = previous.get("commit")
    print(f"mirrored {len(files)} files from omnix {commit[:10]}" + (f" (was {old[:10]})" if old else ""))
    if old and old != commit:
        log = _git(checkout, "log", "--oneline", "--no-decorate", f"{old}..{commit}").decode("utf-8", "replace")
        print(f"\nsync: omnix {old[:10]}..{commit[:10]}\n\n{log}")
    return 0


def command_check(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    lock = _read_lock()
    if not lock.get("commit"):
        print("no sync recorded", file=sys.stderr)
        return 1
    problems: list[str] = []
    for path, digest in lock["files"].items():
        target = ROOT / path
        if not target.is_file():
            problems.append(f"missing  {path}")
        elif sha256(target.read_bytes()) != digest:
            problems.append(f"changed  {path}")
    recorded = set(lock["files"])
    roots = {pattern.split("*", 1)[0].rstrip("/") for pattern in manifest["mirror"]["include"]}
    for root in sorted(roots):
        base = ROOT / root
        candidates = [base] if base.is_file() else (base.rglob("*") if base.is_dir() else [])
        for candidate in candidates:
            if candidate.is_file():
                relative = candidate.relative_to(ROOT).as_posix()
                if mirrored(relative, manifest) and relative not in recorded:
                    problems.append(f"extra    {relative}")
    checkout = Path(args.omnix or lock.get("source") or manifest["source"]["checkout"])
    try:
        blobs = read_blobs(checkout, lock["commit"], derived_sources(manifest))
        for path, data in derived_files(manifest, blobs).items():
            target = ROOT / path
            if not target.is_file() or target.read_bytes() != data:
                problems.append(f"derived  {path}")
    except SyncError as exc:
        problems.append(f"derived  cannot regenerate: {exc}")
    for problem in sorted(set(problems)):
        print(problem)
    if problems:
        print(f"\n{len(set(problems))} difference(s) from omnix {lock['commit'][:10]}; make the change in Omnix and sync.",
              file=sys.stderr)
        return 1
    print(f"mirror matches omnix {lock['commit'][:10]} ({len(lock['files'])} files)")
    return 0


def command_tests(_args: argparse.Namespace) -> int:
    print(TRADING_TESTS.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="mirror an Omnix commit")
    sync.add_argument("--omnix", help="Omnix checkout (default: the manifest's)")
    sync.add_argument("--to", help="commit or ref to mirror (default: the manifest's ref)")
    check = commands.add_parser("check", help="fail on drift from the recorded commit")
    check.add_argument("--omnix", help="Omnix checkout used to regenerate derived files")
    commands.add_parser("tests", help="list the mirrored trading test set")
    args = parser.parse_args(argv)
    try:
        return {"sync": command_sync, "check": command_check, "tests": command_tests}[args.command](args)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
