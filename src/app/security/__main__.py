"""Security maintenance commands."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app.security.auth import AuthService
from app.security.legacy_secret_import import import_legacy_secrets


def _local_auth_service() -> AuthService:
    from app.security.auth import AuthMode, resolve_auth_settings

    settings = resolve_auth_settings()
    if settings.mode is not AuthMode.LOCAL:
        raise SystemExit("This command requires OMNIX_AUTH_MODE=local.")
    return AuthService(settings)


def _import_legacy(args: argparse.Namespace) -> int:
    count, archive = import_legacy_secrets(args.source)
    if archive is None:
        sys.stdout.write("No legacy secret file found.\n")
    else:
        sys.stdout.write(f"Imported {count} credential entries; archived source at {archive}.\n")
    return 0


def _login_code(_args: argparse.Namespace) -> int:
    # Single-use and valid for 60 seconds; the launcher opens
    # /api/auth/local/callback?code=<code> with it.
    sys.stdout.write(_local_auth_service().issue_login_code() + "\n")
    return 0


def _show_install_credential(_args: argparse.Namespace) -> int:
    from app.security.service_credentials import install_credential_path, read_protected_token

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("show-install-credential requires an interactive local console.")
    try:
        credential = read_protected_token(install_credential_path())
    except FileNotFoundError:
        raise SystemExit("No install credential yet; start the gateway with OMNIX_AUTH_MODE=local.") from None
    sys.stdout.write(credential + "\n")
    return 0


def _rotate_install_credential(_args: argparse.Namespace) -> int:
    from app.security.service_credentials import install_credential_path, replace_protected_token

    service = _local_auth_service()
    service.sync_install_credential(replace_protected_token(install_credential_path()))
    sys.stdout.write("Install credential rotated; every local session was signed out.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.security")
    subcommands = parser.add_subparsers(dest="command", required=True)
    importer = subcommands.add_parser("import-legacy-secrets")
    importer.add_argument("--source", type=Path)
    importer.set_defaults(handler=_import_legacy)
    subcommands.add_parser(
        "login-code", help="print a single-use 60-second launcher login code"
    ).set_defaults(handler=_login_code)
    subcommands.add_parser(
        "show-install-credential", help="print the local install credential (console only)"
    ).set_defaults(handler=_show_install_credential)
    subcommands.add_parser(
        "rotate-install-credential", help="replace the install credential and sign out every session"
    ).set_defaults(handler=_rotate_install_credential)
    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
