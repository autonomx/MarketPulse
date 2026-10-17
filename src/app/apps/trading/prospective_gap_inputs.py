"""Prospective-gap decision inputs in PostgreSQL, and the jobs that import them (WP-8.3).

The prospective-gap runtime reads the premarket handoff and the climatology
state only from PostgreSQL. They arrive through explicit imports that record
their provenance (exact content, SHA-256, source, importer):

    python -m app.apps.trading.prospective_gap_inputs import-handoff --file PATH
    python -m app.apps.trading.prospective_gap_inputs import-handoff --github YYYY-MM-DD
    python -m app.apps.trading.prospective_gap_inputs import-climatology --file PATH

The scheduled GitHub import (``trading.prospective_gap_handoff_import``) runs
during the premarket window: it imports today's handoff from
``resources/trading/prospective_gap_inbox/YYYY-MM-DD.json`` and the climatology
state from ``resources/trading/prospective_gap_state/climatology.json`` on the
configured branch, where the research process publishes them. Set
``OMNIX_TRADING_PROSPECTIVE_GAP_HANDOFF_IMPORT=0`` to turn it off.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from app.config.env import env_str
from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work
from app.apps.trading.us_equity_calendar import EASTERN

from .prospective_gap_runtime import ProspectiveClimatologyState, SchedulerPremarketHandoff

IMPORT_TASK_ID = "trading.prospective_gap_handoff_import"
logger = logging.getLogger(__name__)


class HandoffConflict(ValueError):
    """A session already has a different imported handoff."""


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ProspectiveGapInputs:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], AbstractContextManager[PostgresUnitOfWork]] = unit_of_work,
    ) -> None:
        self.uow_factory = uow_factory

    def handoff(self, session_date: date) -> SchedulerPremarketHandoff | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                "SELECT content FROM omnix_trading_premarket_handoffs WHERE session_date = %s",
                (session_date,),
            ).fetchone()
        return SchedulerPremarketHandoff.model_validate_json(row[0]) if row else None

    def climatology_before(self, session_date: date) -> ProspectiveClimatologyState | None:
        """The latest climatology state covering only sessions before ``session_date``."""
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT version, through_session, observation_count, positive_count, probability
                  FROM omnix_trading_climatology_states
                 WHERE through_session < %s
                 ORDER BY through_session DESC
                 LIMIT 1
                """,
                (session_date,),
            ).fetchone()
        if row is None:
            return None
        return ProspectiveClimatologyState(
            version=row[0],
            through_session=row[1],
            observation_count=row[2],
            positive_count=row[3],
            probability=row[4],
        )

    def import_handoff(self, content: str, *, source: str, imported_by: str | None = None) -> SchedulerPremarketHandoff:
        """Store a validated handoff once; the same content again is a no-op."""
        handoff = SchedulerPremarketHandoff.model_validate_json(content)
        digest = _sha256(content)
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_premarket_handoffs (
                    session_date, handoff_version, content, content_sha256, source, imported_by
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_date) DO NOTHING
                """,
                (handoff.session_date, handoff.handoff_version, content, digest, source, imported_by),
            )
            stored = uow.connection.execute(
                "SELECT content_sha256 FROM omnix_trading_premarket_handoffs WHERE session_date = %s",
                (handoff.session_date,),
            ).fetchone()
            if stored[0] != digest:
                uow.rollback()
                raise HandoffConflict(f"premarket handoff for {handoff.session_date} was already imported with other content")
            uow.commit()
        return handoff

    def import_climatology(self, content: str, *, source: str) -> ProspectiveClimatologyState:
        state = ProspectiveClimatologyState.model_validate_json(content)
        with self.uow_factory() as uow:
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_climatology_states (
                    through_session, version, observation_count, positive_count, probability, source
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (through_session) DO UPDATE
                   SET version = EXCLUDED.version,
                       observation_count = EXCLUDED.observation_count,
                       positive_count = EXCLUDED.positive_count,
                       probability = EXCLUDED.probability,
                       source = EXCLUDED.source,
                       imported_at = CURRENT_TIMESTAMP
                """,
                (state.through_session, state.version, state.observation_count, state.positive_count,
                 state.probability, source),
            )
            uow.commit()
        return state


HANDOFF_PATH = "resources/trading/prospective_gap_inbox/{session}.json"
CLIMATOLOGY_PATH = "resources/trading/prospective_gap_state/climatology.json"


def fetch_handoff_from_github(session_date: date) -> tuple[str, str] | None:
    """The scheduler's handoff for ``session_date`` from GitHub, as (content, source); None if absent."""
    return fetch_from_github(HANDOFF_PATH.format(session=session_date.isoformat()))


def github_repository() -> tuple[str, str]:
    """The configured research repository and ref."""
    repository = env_str("OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_REPOSITORY", "autonomx/omnix").strip()
    ref = env_str("OMNIX_TRADING_PROSPECTIVE_GAP_GITHUB_REF", "main").strip()
    if repository.count("/") != 1 or not all(repository.split("/", 1)):
        raise ValueError("invalid_prospective_gap_github_repository")
    if not ref:
        raise ValueError("invalid_prospective_gap_github_ref")
    return repository, ref


def github_token() -> str:
    """The read-only repository token from the protected store or the environment; empty when none is set."""
    from app.security.provider_secret_store import load_trading_provider_secrets

    return str((load_trading_provider_secrets().get("prospective_gap_github") or {}).get("token") or "").strip()


def fetch_from_github(path: str) -> tuple[str, str] | None:
    """A file from the configured repository and ref, as (content, source); None if absent.

    Uses the gh CLI when it is installed; otherwise GitHub's REST API, with the
    stored read-only token when there is one (MarketPulse S-7). A private
    repository read without a token answers 404, which is "not published".
    """
    repository, ref = github_repository()
    gh = shutil.which("gh")
    payload = _fetch_with_gh(gh, repository, ref, path) if gh else _fetch_with_http(repository, ref, path)
    if payload is None:
        return None
    if payload.get("encoding") != "base64":
        raise ValueError("prospective_gap_remote_inbox_requires_base64_content")
    encoded = str(payload.get("content") or "").replace("\n", "")
    try:
        content = base64.b64decode(encoded, validate=True).decode("utf-8")
    except Exception as exc:
        raise ValueError("prospective_gap_remote_inbox_invalid_base64") from exc
    return content, f"github:{repository}@{ref}:{path}:{payload.get('sha', '')}"


def _fetch_with_http(repository: str, ref: str, path: str) -> dict[str, Any] | None:
    import httpx

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = httpx.get(
        f"https://api.github.com/repos/{repository}/contents/{path}",
        params={"ref": ref},
        headers=headers,
        timeout=15.0,
        follow_redirects=False,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise RuntimeError(f"prospective_gap_remote_inbox_fetch_failed:http_{response.status_code}")
    return response.json()


def _fetch_with_gh(gh: str, repository: str, ref: str, path: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        [gh, "api", f"repos/{repository}/contents/{path}?ref={ref}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or "")[-2000:]
        if "404" in detail or "Not Found" in detail:
            return None
        raise RuntimeError(f"prospective_gap_remote_inbox_fetch_failed:{detail}")
    return json.loads(completed.stdout)


def handoff_import_enabled() -> bool:
    return env_str("OMNIX_TRADING_PROSPECTIVE_GAP_HANDOFF_IMPORT", "1").strip().lower() in {"1", "true", "yes", "on"}


def previous_session(session_date: date) -> date:
    """The last U.S. equity trading day before ``session_date``."""
    from datetime import timedelta

    from app.apps.trading.us_equity_calendar import regular_holidays

    day = session_date - timedelta(days=1)
    while day.weekday() >= 5 or day in regular_holidays(day.year):
        day -= timedelta(days=1)
    return day


def import_climatology_from_github(
    inputs: ProspectiveGapInputs,
    session_date: date,
    *,
    fetch: Callable[[str], tuple[str, str] | None] | None = None,
) -> bool:
    """Import the published climatology state unless the one through the previous session is in."""
    current = inputs.climatology_before(session_date)
    if current is not None and current.through_session >= previous_session(session_date):
        return False
    fetched = (fetch or fetch_from_github)(CLIMATOLOGY_PATH)
    if fetched is None:
        return False
    content, source = fetched
    state = ProspectiveClimatologyState.model_validate_json(content)
    if current is not None and state.through_session <= current.through_session:
        return False
    inputs.import_climatology(content, source=source)
    return True


def import_todays_handoff_from_github(
    inputs: ProspectiveGapInputs | None = None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bool:
    """Import today's inputs during the premarket window; True when a handoff was imported."""
    current = now().astimezone(EASTERN)
    if not time(4, 0) <= current.time() <= time(9, 30):
        return False
    inputs = inputs or ProspectiveGapInputs()
    try:
        import_climatology_from_github(inputs, current.date())
    except Exception:
        # A missing climatology update leaves the last state; the handoff still imports.
        logger.warning("prospective-gap climatology import failed", exc_info=True)
    if inputs.handoff(current.date()) is not None:
        return False
    fetched = fetch_handoff_from_github(current.date())
    if fetched is None:
        return False
    content, source = fetched
    inputs.import_handoff(content, source=source, imported_by=IMPORT_TASK_ID)
    return True


def handoff_import_task(context) -> Any:
    """The scheduled GitHub import, or None when it is turned off."""
    if not handoff_import_enabled():
        return None
    from app.runtime.scheduler import ScheduledTaskSpec

    return ScheduledTaskSpec(
        task_id=IMPORT_TASK_ID,
        run=lambda _task_context: import_todays_handoff_from_github(),
        interval_seconds=300.0,
        jitter_seconds=10.0,
        timeout_seconds=60.0,
        executor="thread",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.apps.trading.prospective_gap_inputs", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    handoff = commands.add_parser("import-handoff", help="import a premarket handoff")
    source = handoff.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--github", type=date.fromisoformat, metavar="YYYY-MM-DD")
    climatology = commands.add_parser("import-climatology", help="import a climatology state")
    climatology.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)

    from app.persistence.startup import bootstrap_postgresql_runtime

    bootstrap_postgresql_runtime()
    inputs = ProspectiveGapInputs()
    if args.command == "import-climatology":
        state = inputs.import_climatology(args.file.read_text(encoding="utf-8"), source=f"file:{args.file.resolve()}")
        print(json.dumps({"imported": "climatology", "through_session": state.through_session.isoformat()}))
        return 0
    if args.file is not None:
        content, origin = args.file.read_text(encoding="utf-8"), f"file:{args.file.resolve()}"
    else:
        fetched = fetch_handoff_from_github(args.github)
        if fetched is None:
            print(json.dumps({"imported": None, "reason": "not_found"}))
            return 1
        content, origin = fetched
    imported = inputs.import_handoff(content, source=origin, imported_by="cli")
    print(json.dumps({"imported": "handoff", "session_date": imported.session_date.isoformat(), "source": origin}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
