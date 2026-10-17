"""Stage the runtime the desktop app ships (roadmap Phase 5).

    python build/stage.py all          # everything below, into build/stage/
    python build/stage.py python       # CPython 3.11 + requirements/gateway.lock.txt + ibapi, with python311._pth
    python build/stage.py postgres     # PostgreSQL 17 bin, lib, share (no pgAdmin, docs, headers, symbols)
    python build/stage.py app          # the backend source (src/app, src/marketpulse, the gateway scripts)
    python build/stage.py web          # the built web app (run `npm --prefix web run build` first)
    python build/stage.py check        # import check in the staged interpreter

build/stage/ becomes the installed app's resources folder:

    python/    CPython; python311._pth lists ../app/src and ../app/scripts, so PYTHON* variables are ignored
    postgres/  PostgreSQL binaries
    app/       src/app, src/marketpulse, scripts/run_omnix_gateway.py, scripts/gateway_cluster.py
    web/dist/  the MarketPulse web build
    licenses/  third-party licence texts

Every downloaded archive is checked against build/pins.json before it is used; nothing is fetched unpinned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CACHE = BUILD / "cache"
STAGE = BUILD / "stage"
PINS = json.loads((BUILD / "pins.json").read_text(encoding="utf-8"))
PTH = """python311.zip
.
DLLs
Lib
Lib/site-packages
../app/src
../app/scripts
import site
"""
POSTGRES_KEEP = ("bin", "lib", "share", "server_license.txt", "commandlinetools_3rd_party_licenses.txt")
APP_SOURCES = ("src/app", "src/marketpulse")
APP_SCRIPTS = ("scripts/run_omnix_gateway.py", "scripts/gateway_cluster.py")


class StageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return digest.hexdigest()


def fetch(pin: dict, folder: str) -> Path:
    """The pinned archive from the cache, downloading it once; refused if its SHA-256 differs."""
    target = CACHE / folder / pin["asset"]
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {pin['url']}")
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(pin["url"], timeout=600) as response, partial.open("wb") as stream:
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


def _safe_extract_tar(archive: Path, destination: Path, strip: str) -> None:
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name = member.name
            if not name.startswith(strip + "/"):
                continue
            relative = name[len(strip) + 1:]
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()) or member.issym() or member.islnk():
                raise StageError(f"unsafe archive member {name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as out:  # type: ignore[union-attr]
                    shutil.copyfileobj(source, out)


def stage_python() -> None:
    pin = PINS["python"]
    archive = fetch(pin, "python")
    python_dir = STAGE / "python"
    _reset(python_dir)
    _safe_extract_tar(archive, python_dir, "python")
    interpreter = python_dir / "python.exe"
    if not interpreter.is_file():
        raise StageError("python.exe missing after extraction")
    lock = ROOT / "requirements" / "gateway.lock.txt"
    print(f"installing {lock.relative_to(ROOT)} into the staged interpreter")
    subprocess.run([str(interpreter), "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-warn-script-location",
                    "--disable-pip-version-check", "--no-cache-dir", "-r", str(lock)], check=True)
    stage_ibapi(python_dir / "Lib" / "site-packages")
    # Written last: with a ._pth file the interpreter is isolated and pip's own imports change.
    (python_dir / "python311._pth").write_text(PTH, encoding="utf-8", newline="\n")
    print(f"python staged: {pin['version']}")


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
        f"Version {pin['version']}; its complete source is the python/Lib/site-packages/ibapi folder of this install.\n",
        encoding="utf-8")


def stage_postgres() -> None:
    pin = PINS["postgres"]
    archive = fetch(pin, "postgres")
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
            target = (postgres_dir / relative).resolve()
            if not target.is_relative_to(postgres_dir.resolve()):
                raise StageError(f"unsafe archive member {name}")
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(member) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
    licenses = STAGE / "licenses" / "postgresql"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
        if (postgres_dir / name).is_file():
            shutil.copy2(postgres_dir / name, licenses / name)
    print(f"postgres staged: {pin['version']}")


def stage_app() -> None:
    app_dir = STAGE / "app"
    _reset(app_dir)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test_*.py")
    for relative in APP_SOURCES:
        shutil.copytree(ROOT / relative, app_dir / relative, ignore=ignore)
    for relative in APP_SCRIPTS:
        (app_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, app_dir / relative)
    licenses = STAGE / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, licenses / name)
    # Which code this build is: shown in exported diagnostics.
    lock = json.loads((ROOT / "sync" / "omnix-sync.json").read_text(encoding="utf-8"))
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (STAGE / "build-info.json").write_text(json.dumps({
        "marketpulse_commit": commit or None, "omnix_commit": lock.get("commit"),
        "python": PINS["python"]["version"], "postgres": PINS["postgres"]["version"], "ibapi": PINS["ibapi"]["version"],
    }, indent=2) + "\n", encoding="utf-8")
    print("app staged")


def stage_web() -> None:
    dist = ROOT / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise StageError("web/dist is missing: run `npm --prefix web run build` first")
    target = STAGE / "web" / "dist"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(dist, target)
    print("web staged")


def check() -> None:
    interpreter = STAGE / "python" / "python.exe"
    script = (
        "import sys, app, gateway_cluster, marketpulse, marketpulse.desktop_app, ibapi, psycopg, uvicorn;"
        "from ibapi.client import EClient;"
        "print('imports ok', sys.version.split()[0], sys.flags.isolated or 'pth')"
    )
    subprocess.run([str(interpreter), "-c", script], check=True, cwd=str(STAGE),
                   env={"SystemRoot": __import__("os").environ.get("SystemRoot", r"C:\Windows"),
                        "PYTHONPATH": r"C:\nowhere", "MARKETPULSE_WEB_DIST": str(STAGE / "web" / "dist")})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("part", choices=["all", "python", "postgres", "app", "web", "check"])
    part = parser.parse_args(argv).part
    steps = {"python": stage_python, "postgres": stage_postgres, "app": stage_app, "web": stage_web, "check": check}
    try:
        for name in (["postgres", "python", "app", "web", "check"] if part == "all" else [part]):
            steps[name]()
    except (StageError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
