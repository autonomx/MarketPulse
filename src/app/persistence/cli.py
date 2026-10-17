from __future__ import annotations
from app.config.env import environment_copy as _process_environment

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, NoReturn, Sequence
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .blob_store import LocalBlobStore
from .config import DatabaseSettings, database_settings, migration_database_settings
from .coordinated_recovery import CoordinatedRecoveryRepository
from .cutover_state import PostgresCutoverStateRepository
from .database import PostgresDatabase
from .migrations import apply_migrations, migration_status
from .tenant_scope import system_scope


_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "password",
    "token",
    "secret",
    "credential",
)
_CREDENTIAL_URL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)


class CliUsageError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliUsageError(message)


def _redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SECRET_KEY_PARTS):
        return "[REDACTED]" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(child_key): _redact(child, key=str(child_key)) for child_key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    if isinstance(value, tuple):
        return [_redact(child) for child in value]
    if isinstance(value, str):
        return _CREDENTIAL_URL.sub(r"\g<scheme>***:***@", value)
    return value


def _render(value: object) -> None:
    print(json.dumps(_redact(value), indent=2, sort_keys=True, default=str))


def _require_tool(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"required PostgreSQL tool is not installed: {name}")
    return resolved


def _tool_database_arguments(settings: DatabaseSettings) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(settings.url)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    username = quote(unquote(parsed.username or ""), safe="")
    user = f"{username}@" if username else ""
    port = f":{parsed.port}" if parsed.port else ""
    safe_url = urlunsplit((parsed.scheme, f"{user}{hostname}{port}", parsed.path, parsed.query, ""))
    environment = _process_environment()
    environment.pop("OMNIX_DATABASE_URL", None)
    if parsed.password is not None:
        environment["PGPASSWORD"] = unquote(parsed.password)
    return safe_url, environment


def _run_tool(arguments: list[str], *, environment: dict[str, str] | None = None) -> None:
    completed = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {message[:1000]}" if message else ""
        raise RuntimeError(
            f"PostgreSQL tool failed with exit code {completed.returncode}{suffix}"
        )


def _add_transition_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--software-revision", required=True)
    parser.add_argument("--schema-version", required=True)
    parser.add_argument("--operator-note", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Omnix PostgreSQL operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Check PostgreSQL connectivity")
    migrate = subparsers.add_parser("migrate", help="Apply pending migrations")
    migrate.add_argument("--allow-out-of-order", action="store_true")
    status = subparsers.add_parser("status", help="Show migration state as JSON")
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable JSON migration report (the default output).",
    )
    subparsers.add_parser("verify", help="Require healthy PostgreSQL and zero migration drift")
    retention = subparsers.add_parser(
        "retention", help="Run every enabled retention policy, including maintenance-only ones (audit)"
    )
    retention.add_argument("--batch-size", type=int, default=5000)

    backup = subparsers.add_parser("backup", help="Create a pg_dump custom-format backup")
    backup.add_argument("output", type=Path)

    restore = subparsers.add_parser("restore", help="Restore a custom-format backup")
    restore.add_argument("input", type=Path)
    restore.add_argument("--clean", action="store_true", help="Drop restored objects first")

    cutover = subparsers.add_parser("cutover", help="Inspect or transition PostgreSQL authority")
    cutover_commands = cutover.add_subparsers(dest="cutover_command", required=True)
    cutover_status = cutover_commands.add_parser("status")
    cutover_status.add_argument("--history-limit", type=int, default=100)
    for name in ("mark-imported-unverified", "mark-imported-verified"):
        transition = cutover_commands.add_parser(name)
        _add_transition_arguments(transition)
        transition.add_argument("--legacy-import-run-id", required=True)
    activate = cutover_commands.add_parser("activate-frozen")
    _add_transition_arguments(activate)
    activate.add_argument("--legacy-import-run-id", required=True)
    activate.add_argument("--backup-generation-id", required=True)
    open_writes = cutover_commands.add_parser("open-writes")
    _add_transition_arguments(open_writes)
    open_writes.add_argument("--write-reopen-acknowledged", action="store_true")
    stabilize = cutover_commands.add_parser("stabilize")
    _add_transition_arguments(stabilize)
    stabilize.add_argument("--latest-authoritative-revision", required=True)
    rollback = cutover_commands.add_parser("record-rollback")
    _add_transition_arguments(rollback)
    rollback.add_argument("--destructive-rollback-acknowledged", action="store_true")

    recovery = subparsers.add_parser("recovery", help="Manage coordinated recovery generations")
    recovery_commands = recovery.add_subparsers(dest="recovery_command", required=True)
    create = recovery_commands.add_parser("create-generation")
    create.add_argument("--software-revision", required=True)
    create.add_argument("--schema-version", required=True)
    create.add_argument("--blob-root", type=Path, required=True)
    create.add_argument("--retention-days", type=int, default=30)
    create.add_argument("--rpo-seconds", type=int, default=86_400)
    create.add_argument("--rto-seconds", type=int, default=3_600)
    create.add_argument("--operator-note", required=True)
    encryption = create.add_mutually_exclusive_group(required=True)
    encryption.add_argument(
        "--encryption-required", dest="encryption_required", action="store_true"
    )
    encryption.add_argument(
        "--encryption-not-required", dest="encryption_required", action="store_false"
    )
    capture = recovery_commands.add_parser("capture-manifest")
    capture.add_argument("--backup-generation-id", required=True)
    capture.add_argument("--deletion-grace-days", type=int, default=31)
    copy_blobs = recovery_commands.add_parser("copy-blobs")
    copy_blobs.add_argument("--backup-generation-id", required=True)
    copy_blobs.add_argument("--source-blob-root", type=Path, required=True)
    copy_blobs.add_argument("--destination-blob-root", type=Path, required=True)
    record = recovery_commands.add_parser("record-database-backup")
    record.add_argument("--backup-generation-id", required=True)
    record.add_argument("--postgresql-dump-reference", required=True)
    verify_blobs = recovery_commands.add_parser("verify-blobs")
    verify_blobs.add_argument("--backup-generation-id", required=True)
    verify_blobs.add_argument("--blob-root", type=Path, required=True)
    verify_blobs.add_argument("--database-restore-verified", action="store_true")
    verify_blobs.add_argument("--migrations-verified", action="store_true")
    verify_blobs.add_argument("--smoke-checks-verified", action="store_true")
    recovery_status = recovery_commands.add_parser("status")
    recovery_status.add_argument("--backup-generation-id")
    recovery_status.add_argument("--limit", type=int, default=20)
    return parser


def _cutover_command(args: argparse.Namespace, database: PostgresDatabase) -> dict[str, Any]:
    if args.cutover_command == "status":
        with database.connection() as connection:
            repository = PostgresCutoverStateRepository(connection)
            return {
                "ok": True,
                "cutover": repository.current(),
                "transitions": repository.history(limit=args.history_limit),
            }
    targets = {
        "mark-imported-unverified": "imported_unverified",
        "mark-imported-verified": "imported_verified",
        "activate-frozen": "postgresql_activated_frozen",
        "open-writes": "postgresql_open_for_writes",
        "stabilize": "postgresql_stabilized",
        "record-rollback": "rollback_recorded",
    }
    with database.transaction() as connection:
        result = PostgresCutoverStateRepository(connection).transition(
            to_state=targets[args.cutover_command],
            software_revision=args.software_revision,
            schema_version=args.schema_version,
            import_run_id=getattr(args, "legacy_import_run_id", None),
            backup_generation_id=getattr(args, "backup_generation_id", None),
            latest_authoritative_revision=getattr(
                args, "latest_authoritative_revision", None
            ),
            operator_note=args.operator_note,
            write_reopen_acknowledged=getattr(
                args, "write_reopen_acknowledged", False
            ),
            destructive_acknowledgement=getattr(
                args, "destructive_rollback_acknowledged", False
            ),
        )
    return {"ok": True, "cutover": result}


def _recovery_command(args: argparse.Namespace, database: PostgresDatabase) -> dict[str, Any]:
    if args.recovery_command == "status":
        with database.connection() as connection:
            generations = CoordinatedRecoveryRepository(connection).status(
                args.backup_generation_id,
                limit=args.limit,
            )
        return {"ok": True, "generations": generations}
    with database.transaction() as connection:
        repository = CoordinatedRecoveryRepository(connection)
        if args.recovery_command == "create-generation":
            generation_id = repository.create_generation(
                software_revision=args.software_revision,
                schema_version=args.schema_version,
                blob_root=args.blob_root,
                retention_days=args.retention_days,
                rpo_seconds=args.rpo_seconds,
                rto_seconds=args.rto_seconds,
                encryption_required=args.encryption_required,
                operator_note=args.operator_note,
            )
            result: dict[str, Any] = {"generation_id": generation_id}
        elif args.recovery_command == "capture-manifest":
            result = repository.capture_manifest(
                args.backup_generation_id,
                deletion_grace_days=args.deletion_grace_days,
            )
        elif args.recovery_command == "copy-blobs":
            result = repository.copy_manifested_blobs(
                args.backup_generation_id,
                source=LocalBlobStore(args.source_blob_root),
                destination=LocalBlobStore(args.destination_blob_root),
            )
        elif args.recovery_command == "record-database-backup":
            repository.record_database_backup(
                args.backup_generation_id,
                args.postgresql_dump_reference,
            )
            result = {
                "generation_id": args.backup_generation_id,
                "database_backup_reference": args.postgresql_dump_reference,
            }
        elif args.recovery_command == "verify-blobs":
            result = repository.verify_blobs(
                args.backup_generation_id,
                LocalBlobStore(args.blob_root),
                database_restore_verified=args.database_restore_verified,
                migrations_verified=args.migrations_verified,
                smoke_checks_verified=args.smoke_checks_verified,
            )
        else:  # pragma: no cover
            raise RuntimeError(f"unsupported recovery command: {args.recovery_command}")
    return {"ok": result.get("ok", True), "recovery": result}


def main(argv: Sequence[str] | None = None) -> int:
    database: PostgresDatabase | None = None
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
        settings = (
            migration_database_settings()
            # Backups read every workspace and restores recreate the schema, so
            # both use the DDL owner like migrations (row-level security, WP-4.4).
            if args.command in {"migrate", "status", "verify", "backup", "restore", "retention"}
            else database_settings()
        )
        database = PostgresDatabase(settings)
        if args.command == "health":
            report = database.health()
            _render(report)
            return 0 if report.get("ok") is True else 1
        if args.command == "migrate":
            report = apply_migrations(database, allow_out_of_order=args.allow_out_of_order)
            _render(report)
            return 0 if report.get("ok") is True else 1
        if args.command == "status":
            report = migration_status(database)
            _render(report)
            return 0 if report.get("ok") is True else 1
        if args.command == "verify":
            health = database.health()
            if health.get("ok") is not True:
                _render({"ok": False, "health": health})
                return 1
            status = migration_status(database)
            ok = status.get("ok") is True and status.get("pending") == []
            _render({"ok": ok, "health": health, "migrations": status})
            return 0 if ok else 1
        if args.command == "retention":
            from .retention import RetentionWorker

            outcome = RetentionWorker(database, batch_size=args.batch_size).run_once(include_maintenance=True)
            _render({"ok": True, "run_id": outcome.run_id, "deleted": outcome.deleted, "skipped": outcome.skipped})
            return 0
        if args.command in {"backup", "restore"}:
            safe_url, environment = _tool_database_arguments(settings)
            if args.command == "backup":
                args.output.parent.mkdir(parents=True, exist_ok=True)
                _run_tool(
                    [
                        _require_tool("pg_dump"),
                        "--format=custom",
                        "--no-owner",
                        "--no-acl",
                        f"--file={args.output}",
                        f"--dbname={safe_url}",
                    ],
                    environment=environment,
                )
                report = {"ok": True, "backup": str(args.output.resolve())}
            else:
                if not args.input.is_file():
                    raise RuntimeError(f"backup does not exist: {args.input}")
                arguments = [
                    _require_tool("pg_restore"),
                    "--no-owner",
                    "--no-acl",
                    f"--dbname={safe_url}",
                ]
                if args.clean:
                    arguments.extend(["--clean", "--if-exists"])
                arguments.append(str(args.input))
                _run_tool(arguments, environment=environment)
                report = {"ok": True, "restored": str(args.input.resolve())}
            _render(report)
            return 0
        with system_scope("operator.cli"):
            if args.command == "cutover":
                report = _cutover_command(args, database)
            elif args.command == "recovery":
                report = _recovery_command(args, database)
            else:  # pragma: no cover
                raise RuntimeError(f"unsupported command: {args.command}")
        _render(report)
        return 0 if report.get("ok") is True else 1
    except Exception as exc:
        _render({"ok": False, "error": exc.__class__.__name__, "message": str(exc)})
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
