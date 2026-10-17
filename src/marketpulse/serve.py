"""The one Python entry point the desktop supervisor calls (roadmap §4.3).

    python -m marketpulse.serve serve       --config launch.json   # the gateway, its job worker and schedulers
    python -m marketpulse.serve migrate     --config launch.json   # release migrations, as the owner role
    python -m marketpulse.serve status      --config launch.json   # the migration report (JSON), as the owner role
    python -m marketpulse.serve login-code  --config launch.json   # first run only: the owner's one-time sign-in code
    python -m marketpulse.serve backup PATH --config launch.json   # pg_dump custom format, as the owner role
    python -m marketpulse.serve restore PATH --config launch.json  # then the runtime role's grants are re-applied
    python -m marketpulse.serve grants      --config launch.json   # re-apply migration 0101's runtime grants
    python -m marketpulse.serve env         --config launch.json   # the child environment, secrets redacted
    python -m marketpulse.serve import-reference [--apply] --config launch.json
        # Omnix's market reference data (MP-10.1) from MARKETPULSE_IMPORT_SOURCE_URL; a dry run without --apply
    python -m marketpulse.serve import-account-data [--apply] [--source-workspace ID] --config launch.json
        # one Omnix workspace's documents, alerts and paper accounts (MP-10.2), into the owner's workspace

Each command first replaces this process's environment with marketpulse.env.child_environment, so the gateway, the
job worker it starts and the database tools all run with exactly that environment.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

from marketpulse.env import DB_APP, LaunchConfig, apply_environment, child_environment, gateway_command

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
_REDACT = ("PASSWORD", "SECRET", "TOKEN", "DATABASE_URL")


def _prepare(config_path: Path, *, owner: bool = True) -> LaunchConfig:
    config = LaunchConfig.load(config_path)
    env = child_environment(config, owner=owner)
    # Under the bundled interpreter the ._pth file sets the import roots and ignores PYTHONPATH; a development
    # interpreter needs it for the job worker, which starts with the same executable.
    env["PYTHONPATH"] = f"{SRC}{';' if sys.platform == 'win32' else ':'}{SCRIPTS}"
    apply_environment(env)
    for folder in (config.data_dir / "resources", config.data_dir / "secrets"):
        folder.mkdir(parents=True, exist_ok=True)
    for path in (str(SRC), str(SCRIPTS)):
        if path not in sys.path:
            sys.path.insert(0, path)
    return config


def _serve(config: LaunchConfig) -> int:
    sys.argv = gateway_command(config, sys.executable, SCRIPTS)[1:]
    try:
        runpy.run_path(str(SCRIPTS / "run_omnix_gateway.py"), run_name="__main__")
    except SystemExit as exit_:
        return int(exit_.code or 0)
    return 0


def _persistence(argv: list[str]) -> int:
    from app.persistence.cli import main

    return main(argv)


def _grants() -> int:
    """Migration 0101's runtime grants; pg_dump --no-acl drops them, so a restore needs them again."""
    import psycopg

    from app.persistence.config import migration_database_settings

    with psycopg.connect(migration_database_settings().url, autocommit=True) as connection:
        connection.execute(f"GRANT USAGE ON SCHEMA public TO {DB_APP}")
        connection.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {DB_APP}")
        connection.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {DB_APP}")
        connection.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {DB_APP}")
        connection.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {DB_APP}")
    print(json.dumps({"grants": "applied", "role": DB_APP}))
    return 0


def _login_code() -> int:
    from app.security.auth import AuthMode, AuthService, resolve_auth_settings

    settings = resolve_auth_settings()
    if settings.mode is not AuthMode.LOCAL:
        raise SystemExit("login codes need OMNIX_AUTH_MODE=local")
    print(AuthService(settings).issue_login_code())
    return 0


def _import_reference(source_url: str | None, *, apply: bool) -> int:
    """Prints {"applied", "tables"} or {"error"}: a refused import is a result for the shell to show, not a crash."""
    import psycopg

    from app.persistence.config import migration_database_settings
    from marketpulse.import_omnix import ReferenceImportError, import_reference

    if not source_url:
        print(json.dumps({"error": "no source: MARKETPULSE_IMPORT_SOURCE_URL is not set"}))
        return 0
    try:
        tables = import_reference(source_url, migration_database_settings().url, apply=apply)
    except ReferenceImportError as exc:
        print(json.dumps({"error": str(exc)}))
        return 0
    except psycopg.OperationalError as exc:
        print(json.dumps({"error": f"could not reach a database: {str(exc).strip()}"}))
        return 0
    print(json.dumps({"applied": apply, "tables": tables}))
    return 0


def _import_account_data(source_url: str | None, *, apply: bool, source_workspace: str | None) -> int:
    """Prints the import report or {"error"}, as _import_reference does."""
    import psycopg

    from app.persistence.config import migration_database_settings
    from marketpulse.import_omnix_accounts import LOCAL_WORKSPACE, AccountImportError, import_account_data

    if not source_url:
        print(json.dumps({"error": "no source: MARKETPULSE_IMPORT_SOURCE_URL is not set"}))
        return 0
    try:
        report = import_account_data(source_url, migration_database_settings().url, apply=apply,
                                     source_workspace=source_workspace or LOCAL_WORKSPACE)
    except AccountImportError as exc:
        print(json.dumps({"error": str(exc)}))
        return 0
    except psycopg.Error as exc:
        print(json.dumps({"error": f"the import stopped: {str(exc).strip()}"}))
        return 0
    print(json.dumps(report, default=str))
    return 0


def _show_env() -> int:
    import os

    shown = {name: ("***" if any(marker in name for marker in _REDACT) else value)
             for name, value in sorted(os.environ.items())}
    print(json.dumps(shown, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m marketpulse.serve", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["serve", "migrate", "status", "login-code", "backup", "restore", "grants", "env",
                                            "import-reference", "import-account-data"])
    parser.add_argument("path", nargs="?", type=Path, help="backup or restore file")
    parser.add_argument("--config", required=True, type=Path, help="the supervisor's launch config (JSON)")
    parser.add_argument("--apply", action="store_true", help="imports: write the rows (default: a dry run)")
    parser.add_argument("--source-workspace", help="import-account-data: the Omnix workspace (default workspace:local)")
    args = parser.parse_args(argv)
    # Read before _prepare replaces the environment (it drops MARKETPULSE_* variables).
    import_source = os.environ.get("MARKETPULSE_IMPORT_SOURCE_URL")
    # The gateway runs without the owner role's URL; the one-shot commands (migrate, backup, imports, …) need it.
    config = _prepare(args.config, owner=args.command != "serve")
    if args.command == "serve":
        return _serve(config)
    if args.command == "migrate":
        return _persistence(["migrate"])
    if args.command == "status":
        return _persistence(["status"])
    if args.command in {"backup", "restore"}:
        if args.path is None:
            parser.error(f"{args.command} needs a file")
        code = _persistence([args.command, str(args.path)] + (["--clean"] if args.command == "restore" else []))
        return _grants() if code == 0 and args.command == "restore" else code
    if args.command == "grants":
        return _grants()
    if args.command == "login-code":
        return _login_code()
    if args.command == "import-reference":
        return _import_reference(import_source, apply=args.apply)
    if args.command == "import-account-data":
        return _import_account_data(import_source, apply=args.apply, source_workspace=args.source_workspace)
    return _show_env()


if __name__ == "__main__":
    raise SystemExit(main())
