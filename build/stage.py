"""Stage the runtime the desktop app ships (roadmap Phases 5 and 11).

    python build/stage.py all [--platform windows-x64|macos-arm64|macos-x64]
    python build/stage.py python       # CPython 3.11 + requirements/gateway.lock.txt + ibapi, isolated import roots
    python build/stage.py postgres     # PostgreSQL 17 bin, lib, share (no pgAdmin, docs, headers, symbols)
    python build/stage.py prune        # drop what the running app never loads (debug symbols, pip, Tk, tests, …)
    python build/stage.py app          # the backend source (src/app, src/marketpulse, the gateway scripts)
    python build/stage.py web          # the built web app (run `npm --prefix web run build` first)
    python build/stage.py check        # import check in the staged interpreter

The platform defaults to the machine's. Python's dependencies are installed by the staged interpreter itself, so a
platform's runtime is staged on that platform (macOS on the owner's Mac).

build/stage/ becomes the installed app's resources folder (``runtime``):

    python/    CPython; Windows: python311._pth lists ../app/src and ../app/scripts (PYTHON* variables ignored);
               macOS: site-packages/marketpulse.pth adds the same roots
    postgres/  PostgreSQL binaries
    app/       src/app, src/marketpulse, scripts/run_omnix_gateway.py, scripts/gateway_cluster.py
    web/dist/  the MarketPulse web build
    licenses/  third-party licence texts
    build-info.json  the MarketPulse and Omnix commits and pinned versions

Every downloaded archive is checked against build/pins.json before it is used; nothing is fetched unpinned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CACHE = BUILD / "cache"
STAGE = BUILD / "stage"
PINS = json.loads((BUILD / "pins.json").read_text(encoding="utf-8"))
WINDOWS_PTH = """python311.zip
.
DLLs
Lib
Lib/site-packages
../app/src
../app/scripts
import site
"""
# Relative to python/lib/python3.11/site-packages.
MACOS_PTH = "../../../../app/src\n../../../../app/scripts\n"
POSTGRES_KEEP = ("bin", "lib", "share", "server_license.txt", "commandlinetools_3rd_party_licenses.txt")
APP_SCRIPTS = ("scripts/run_omnix_gateway.py", "scripts/gateway_cluster.py")
# The longest path below the stage that a Windows install may hold. NSIS cannot remove paths over 260 characters, so
# desktop/build-resources/installer.nsh caps the install folder at 112 characters: 260, less 19 for the
# \resources\runtime\ between them, less this, less 1.
MAX_RELATIVE_PATH = 128
# What the running app never loads (roadmap Phase 12's installer size), as globs below python/ and postgres/.
PRUNE_PYTHON = (
    "**/*.pdb",  # debug symbols
    "tcl", "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "Lib/tkinter", "Lib/idlelib", "Lib/turtledemo",
    "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/python3.11/tkinter", "lib/python3.11/idlelib",
    "lib/python3.11/turtledemo", "lib/python3.11/lib-dynload/_tkinter*",
    # The installer's tools: dependencies are installed at staging, never at run time.
    "*/site-packages/pip", "*/site-packages/pip-*.dist-info", "*/site-packages/setuptools",
    "*/site-packages/setuptools-*.dist-info", "*/site-packages/_distutils_hack", "*/site-packages/distutils-precedence.pth",
    "lib/python3.11/site-packages/pip", "lib/python3.11/site-packages/pip-*.dist-info",
    "lib/python3.11/site-packages/setuptools", "lib/python3.11/site-packages/setuptools-*.dist-info",
    "lib/python3.11/site-packages/_distutils_hack", "lib/python3.11/site-packages/distutils-precedence.pth",
    # Third-party packages' own test suites.
    "**/site-packages/*/tests", "**/site-packages/*/*/tests", "**/site-packages/*/*/*/tests",
)
# Packages only Omnix's other features use (embeddings, speech, documents, images, smart plugs). Trading never
# imports them: a trading-only boot loads none, and all 2148 tests then in sync/trading-tests.txt pass with them made
# unimportable. numpy stays (gateway-level code reaches it).
TRADING_UNUSED = (
    "onnxruntime", "onnxruntime-*.dist-info", "tokenizers", "tokenizers-*.dist-info",
    "huggingface_hub", "huggingface_hub-*.dist-info", "hf_xet", "hf_xet-*.dist-info",
    "soundfile.py", "_soundfile.py", "_soundfile_data", "soundfile-*.dist-info", "pypdf", "pypdf-*.dist-info",
    "docx", "python_docx-*.dist-info", "kasa", "python_kasa-*.dist-info", "PIL", "pillow.libs", "pillow-*.dist-info",
    "pandas", "pandas.libs", "pandas-*.dist-info", "lxml", "lxml-*.dist-info", "fsspec", "fsspec-*.dist-info",
)
PRUNE_PYTHON += tuple(f"{site}/{name}" for site in ("Lib/site-packages", "lib/python3.11/site-packages")
                      for name in TRADING_UNUSED)
PRUNE_POSTGRES = (
    # pgAdmin's StackBuilder and its wxWidgets, embedded-SQL tooling, benchmarks and test helpers.
    "bin/stackbuilder*", "bin/wx*", "bin/ecpg*", "bin/libecpg*", "bin/libpgtypes*", "bin/pgbench*", "bin/pg_test_*",
    "bin/test_*", "bin/testplug*",
    # Link-time libraries and message translations (the cluster runs with lc_messages C).
    "bin/*.lib", "lib/*.lib", "lib/*.a", "share/locale",
)


class StageError(RuntimeError):
    pass


def host_platform() -> str:
    if sys.platform == "win32":
        return "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine() == "arm64" else "macos-x64"
    raise StageError(f"no runtime is defined for {sys.platform}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    """Files in Windows order (case-insensitive) on every platform, so a pin made on Windows also holds on a Mac."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda path: PureWindowsPath(path.relative_to(root))):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return digest.hexdigest()


def fetch(family: str, target_platform: str) -> Path:
    """The pinned archive for a platform from the cache, downloading it once; refused if its SHA-256 differs."""
    pin = PINS[family]["platforms"][target_platform]
    url = PINS[family]["url"].format(asset=pin["asset"])
    folder = family if target_platform == "windows-x64" else f"{family}-macos"
    target = CACHE / folder / pin["asset"]
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url}")
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url, timeout=600) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        partial.replace(target)
    actual = sha256_file(target)
    if actual != pin["sha256"]:
        raise StageError(f"{target.name}: SHA-256 {actual} does not match the pin {pin['sha256']}")
    return target


def _reset(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _inside(base: Path, target: Path) -> bool:
    return target.resolve().is_relative_to(base.resolve())


def _safe_extract_tar(archive: Path, destination: Path, strip: str) -> None:
    """Extracts members under ``strip/``; symlinks only when they stay inside the destination."""
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.name.startswith(strip + "/"):
                continue
            relative = member.name[len(strip) + 1:]
            target = destination / relative
            if not _inside(destination, target) or member.islnk():
                raise StageError(f"unsafe archive member {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                if not _inside(destination, target.parent / member.linkname):
                    raise StageError(f"symlink leaves the stage: {member.name} -> {member.linkname}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(member.linkname, target)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as out:  # type: ignore[union-attr]
                    shutil.copyfileobj(source, out)
                if member.mode & 0o111:
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def python_paths(target_platform: str) -> tuple[Path, Path, Path]:
    """(interpreter, site-packages, import-roots file) of the staged Python."""
    python_dir = STAGE / "python"
    if target_platform == "windows-x64":
        return python_dir / "python.exe", python_dir / "Lib" / "site-packages", python_dir / "python311._pth"
    site = python_dir / "lib" / "python3.11" / "site-packages"
    return python_dir / "bin" / "python3", site, site / "marketpulse.pth"


def stage_python(target_platform: str) -> None:
    if target_platform != host_platform():
        raise StageError(f"stage the {target_platform} Python on that platform (its interpreter installs the lock)")
    archive = fetch("python", target_platform)
    _reset(STAGE / "python")
    _safe_extract_tar(archive, STAGE / "python", "python")
    interpreter, site_packages, roots = python_paths(target_platform)
    if not interpreter.exists():
        raise StageError(f"{interpreter.name} missing after extraction")
    lock = ROOT / "requirements" / "gateway.lock.txt"
    print(f"installing {lock.relative_to(ROOT)} into the staged interpreter")
    subprocess.run([str(interpreter), "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-warn-script-location",
                    "--disable-pip-version-check", "--no-cache-dir", "-r", str(lock)], check=True)
    stage_ibapi(site_packages)
    # Written last: on Windows a ._pth file isolates the interpreter, which changes pip's own imports.
    roots.write_text(WINDOWS_PTH if target_platform == "windows-x64" else MACOS_PTH, encoding="utf-8", newline="\n")
    print(f"python staged: {PINS['python']['version']} ({target_platform})")


def stage_ibapi(site_packages: Path) -> None:
    pin = PINS["ibapi"]
    source = ROOT / pin["source_dir"]
    package = source / "ibapi"
    if not package.is_dir():
        raise StageError(f"{package} is missing: copy Interactive Brokers' official ibapi {pin['version']} there (see pins.json)")
    actual = tree_sha256(package)
    if actual != pin["tree_sha256"]:
        raise StageError(f"ibapi files do not match the pin ({actual})")
    target = site_packages / "ibapi"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(package, target, ignore=shutil.ignore_patterns("__pycache__"))
    info = site_packages / f"ibapi-{pin['version']}.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text((source / "PKG-INFO").read_text(encoding="utf-8"), encoding="utf-8")
    (info / "INSTALLER").write_text("marketpulse-stage\n", encoding="utf-8")
    licenses = STAGE / "licenses" / "ibapi"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "LICENSE-GPL-3.0.txt", licenses / "LICENSE-GPL-3.0.txt")
    # GPL: the client's complete source ships alongside (it is pure Python, so the installed files are the source).
    (licenses / "SOURCE.txt").write_text(
        "The Interactive Brokers Python API client (ibapi) is bundled under GPL-3.0-or-later.\n"
        f"Version {pin['version']}; its complete source is the ibapi folder of the bundled Python's site-packages.\n",
        encoding="utf-8")


def stage_postgres(target_platform: str) -> None:
    archive = fetch("postgres", target_platform)
    postgres_dir = STAGE / "postgres"
    _reset(postgres_dir)
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            name = member.filename.replace("\\", "/")
            if not name.startswith("pgsql/"):
                continue
            relative = name[len("pgsql/"):]
            if not relative or relative.split("/", 1)[0] not in POSTGRES_KEEP:
                continue
            target = postgres_dir / relative
            if not _inside(postgres_dir, target):
                raise StageError(f"unsafe archive member {name}")
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                # macOS archives store dylib version links as symlinks.
                link = zipped.read(member).decode("utf-8")
                if not _inside(postgres_dir, target.parent / link):
                    raise StageError(f"symlink leaves the stage: {name} -> {link}")
                if sys.platform != "win32":
                    os.symlink(link, target)
                elif (target.parent / link).exists():
                    # A Windows test extraction of the macOS archive: a copy stands in for the link.
                    shutil.copyfile(target.parent / link, target)
                continue
            with zipped.open(member) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
            if mode & 0o111:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    licenses = STAGE / "licenses" / "postgresql"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
        if (postgres_dir / name).is_file():
            shutil.copy2(postgres_dir / name, licenses / name)
    print(f"postgres staged: {PINS['postgres']['version']} ({target_platform})")


def _remove(root: Path, patterns: tuple[str, ...]) -> int:
    """Deletes what the globs match below root; returns the bytes freed."""
    freed = 0
    for pattern in patterns:
        for path in sorted(root.glob(pattern), key=lambda found: len(found.parts), reverse=True):
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_dir() and not path.is_symlink():
                freed += sum(child.stat().st_size for child in path.rglob("*") if child.is_file() and not child.is_symlink())
                shutil.rmtree(path)
            else:
                freed += path.lstat().st_size
                path.unlink()
    return freed


def prune(_target_platform: str) -> None:
    python_freed = _remove(STAGE / "python", PRUNE_PYTHON) if (STAGE / "python").is_dir() else 0
    postgres_freed = _remove(STAGE / "postgres", PRUNE_POSTGRES) if (STAGE / "postgres").is_dir() else 0
    print(f"pruned {python_freed / 2**20:.0f} MB from python, {postgres_freed / 2**20:.0f} MB from postgres")


def _test_file(relative: str) -> bool:
    parts = relative.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_")


def stage_app(target_platform: str) -> None:
    app_dir = STAGE / "app"
    _reset(app_dir)
    lock = json.loads((ROOT / "sync" / "omnix-sync.json").read_text(encoding="utf-8"))
    # Mirrored code comes from the sync lock's file list, never the working tree wholesale: a stray or unrecorded
    # file cannot reach the installer. MarketPulse's own package is copied whole.
    mirrored = [path for path in lock["files"] if path.startswith("src/app/") and not _test_file(path)]
    for relative in [*sorted(mirrored), *APP_SCRIPTS]:
        if relative not in lock["files"]:
            raise StageError(f"{relative} is not in the sync lock")
        (app_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, app_dir / relative)
    shutil.copytree(ROOT / "src" / "marketpulse", app_dir / "src" / "marketpulse",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test_*.py"))
    licenses = STAGE / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, licenses / name)
    # Which code this build is: shown in exported diagnostics.
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (STAGE / "build-info.json").write_text(json.dumps({
        "platform": target_platform, "marketpulse_commit": commit or None, "omnix_commit": lock.get("commit"),
        "python": PINS["python"]["version"], "postgres": PINS["postgres"]["version"], "ibapi": PINS["ibapi"]["version"],
    }, indent=2) + "\n", encoding="utf-8")
    print("app staged")


# Chunks of Omnix's other features (roadmap Phase 4 gate): the MarketPulse build registers trading only.
OTHER_FEATURE_CHUNK = re.compile(r"(?:^|[a-z_])(?:Rpg|Audiobook|Assistant|Voice|Story|Companion|Character)"
                                 r"|^(?:rpg|audiobook|assistant|voice|story|companion|character)")


def stage_web(_target_platform: str) -> None:
    dist = ROOT / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise StageError("web/dist is missing: run `npm --prefix web run build` first")
    others = sorted(path.name for path in (dist / "assets").iterdir() if OTHER_FEATURE_CHUNK.search(path.name))
    if others:
        raise StageError(f"web/dist carries other features' chunks: {', '.join(others)}")
    target = STAGE / "web" / "dist"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(dist, target)
    print("web staged")


def check(target_platform: str) -> None:
    interpreter, _, _ = python_paths(target_platform)
    longest = max((path for path in STAGE.rglob("*") if "__pycache__" not in path.parts),
                  key=lambda path: len(str(path.relative_to(STAGE))))
    if len(str(longest.relative_to(STAGE))) > MAX_RELATIVE_PATH:
        raise StageError(f"{longest.relative_to(STAGE)} is longer than {MAX_RELATIVE_PATH} characters (see MAX_RELATIVE_PATH)")
    script = (
        "import sys, app, gateway_cluster, marketpulse, marketpulse.desktop_app, ibapi, psycopg, uvicorn;"
        "from ibapi.client import EClient;"
        "print('imports ok', sys.version.split()[0])"
    )
    env = {"MARKETPULSE_WEB_DIST": str(STAGE / "web" / "dist"), "PYTHONPATH": str(Path("/nowhere"))}
    if sys.platform == "win32":
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
    subprocess.run([str(interpreter), "-c", script], check=True, cwd=str(STAGE), env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("part", choices=["all", "python", "postgres", "prune", "app", "web", "check"])
    parser.add_argument("--platform", choices=["windows-x64", "macos-arm64", "macos-x64"], default=None)
    args = parser.parse_args(argv)
    steps = {"python": stage_python, "postgres": stage_postgres, "prune": prune, "app": stage_app, "web": stage_web,
             "check": check}
    try:
        target_platform = args.platform or host_platform()
        for name in (["postgres", "python", "prune", "app", "web", "check"] if args.part == "all" else [args.part]):
            steps[name](target_platform)
    except (StageError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
