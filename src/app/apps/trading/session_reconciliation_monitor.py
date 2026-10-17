"""Automatic formal-session SIP reconciliation.

The monitor freezes the exact strategy/event/universe population after the
session, then retries only the authoritative consolidated-SIP evidence contract.
It never substitutes IEX, web quotes, adjusted daily bars, or partial prints.
"""

from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .feature_qualification import FeatureRequirement, qualify_bar_feature
from .providers.alpaca_sip import AlpacaSipResearchProvider
from .prospective_experiment_outcomes import evaluate_geometry_on_sip_tape
from .prospective_prediction_evidence import select_analysis_session_prices
from .prospective_prediction_scoring import build_formal_outcome_labels
from .session_evidence import (
    EvidenceInput,
    SessionEvidenceManifest,
    SessionEvidenceRepository,
    begin_reconciliation,
    build_session_evidence_manifest,
    checkpoint_reconciliation,
    default_session_evidence_repository,
    defer_reconciliation,
    event_stream_input,
    finalize_reconciliation,
    permanently_unscorable,
    provider_evidence_input,
)
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import resolve_v2_evidence_archive_for_session
from .trade_logging import trade_log
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time

logger = logging.getLogger(__name__)


_STATE_KEY = "_omnix_trading_session_reconciliation_monitor"
_DEFAULT_CREATE_AFTER = time(16, 5)
_ATTEMPT_TIMEOUT_SECONDS = 1800.0


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def session_reconciliation_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_SESSION_RECONCILIATION_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_SESSION_RECONCILIATION_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(
            _env_str(
                "OMNIX_TRADING_SESSION_RECONCILIATION_INTERVAL_SECONDS",
                "300",
            )
        )
    except ValueError:
        value = 300.0
    return max(60.0, value)


def _retry_minutes() -> int:
    try:
        value = int(
            _env_str(
                "OMNIX_TRADING_SESSION_RECONCILIATION_RETRY_MINUTES",
                "15",
            )
        )
    except ValueError:
        value = 15
    return max(5, value)


def _max_age_days() -> int:
    try:
        value = int(
            _env_str(
                "OMNIX_TRADING_SESSION_RECONCILIATION_MAX_AGE_DAYS",
                "7",
            )
        )
    except ValueError:
        value = 7
    return max(1, value)


def _session_bounds(session_date: date) -> tuple[datetime, datetime]:
    start_et = datetime.combine(session_date, time.min, tzinfo=_ET)
    end_et = start_et + timedelta(days=1)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _formal_close(session_date: date) -> datetime:
    return datetime.combine(
        session_date,
        regular_close_time(session_date),
        tzinfo=_ET,
    ).astimezone(timezone.utc)


def _confirmed_nontrading_starts(
    bars,
    trades,
    *,
    session_date: date,
) -> tuple[datetime, ...]:
    session_start = datetime.combine(
        session_date,
        time(9, 30),
        tzinfo=_ET,
    ).astimezone(timezone.utc)
    session_end = _formal_close(session_date)
    bar_starts = {
        bar.start_time.astimezone(timezone.utc)
        for bar in bars
        if bar.is_final and bar.session == "regular"
    }
    trade_times = [
        trade.event_timestamp.astimezone(timezone.utc)
        for trade in trades
    ]
    confirmed: list[datetime] = []
    cursor = session_start
    step = timedelta(minutes=5)
    while cursor < session_end:
        if cursor not in bar_starts:
            has_trade = any(cursor <= value < cursor + step for value in trade_times)
            if not has_trade:
                confirmed.append(cursor)
        cursor += step
    return tuple(confirmed)


def _complete_sip_5m_certificate(
    bars,
    trades,
    *,
    instrument_id: str,
    session_date: date,
):
    confirmed_nontrading = _confirmed_nontrading_starts(
        bars,
        trades,
        session_date=session_date,
    )
    return qualify_bar_feature(
        bars,
        FeatureRequirement(
            requirement_id="formal-sip-5m-session-v1",
            feature_name="formal_sip_5m_session",
            interval="5m",
            dependency_class="EVENT_SEQUENCE",
            source_policy=("alpaca_sip",),
        ),
        instrument_id=instrument_id,
        session_date=session_date,
        observed_at=_formal_close(session_date) + timedelta(seconds=1),
        confirmed_nontrading_starts=confirmed_nontrading,
    )


def _parse_datetime(value: object, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value not in {None, ""}:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None:
        return fallback
    return parsed.astimezone(timezone.utc)


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        logger.debug("suppressed error in %s", "_decimal", exc_info=True)
        return None


def _prospective_experiment_outcomes(
    events: list[StrategyEvent],
    trades,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "ai_v3_geometry_challenger":
            comparison = payload.get("comparison")
            if not isinstance(comparison, dict):
                continue
            challenger = comparison.get("challenger_geometry")
            if not isinstance(challenger, dict):
                continue
            entry = _decimal(challenger.get("entry_reference"))
            invalidation = _decimal(challenger.get("invalidation_price"))
            if entry is None or invalidation is None or invalidation >= entry:
                continue
            try:
                challenger_outcome = evaluate_geometry_on_sip_tape(
                    trades,
                    started_at=event.observed_at,
                    entry_reference=entry,
                    invalidation_price=invalidation,
                )
            except Exception:
                logger.debug("suppressed error in %s", "_prospective_experiment_outcomes", exc_info=True)
                continue

            champion_outcome = None
            source_decision = payload.get("source_v2_decision")
            champion_invalidation = (
                _decimal(source_decision.get("invalidation_price"))
                if isinstance(source_decision, dict)
                else None
            )
            if (
                champion_invalidation is not None
                and champion_invalidation < entry
            ):
                try:
                    champion_outcome = evaluate_geometry_on_sip_tape(
                        trades,
                        started_at=event.observed_at,
                        entry_reference=entry,
                        invalidation_price=champion_invalidation,
                    )
                except Exception:
                    champion_outcome = None

            records.append(
                {
                    "experiment": "runner_geometry_challenger",
                    "event_id": event.event_id,
                    "observed_at": event.observed_at.isoformat(),
                    "setup_family": comparison.get("setup_family"),
                    "champion_action": comparison.get("champion_action"),
                    "challenger_action": comparison.get("challenger_action"),
                    "champion_outcome": (
                        champion_outcome.model_dump(mode="json")
                        if champion_outcome is not None
                        else None
                    ),
                    "challenger_outcome": challenger_outcome.model_dump(
                        mode="json"
                    ),
                    "entry_clock_precision": "source_v2_event_observed_at",
                }
            )
            continue

        if event.event_type == "ai_v3_agreement_cohort":
            geometry = payload.get("reference_geometry")
            if not isinstance(geometry, dict):
                continue
            entry = _decimal(geometry.get("entry_reference"))
            invalidation = _decimal(geometry.get("invalidation_price"))
            if entry is None or invalidation is None or invalidation >= entry:
                continue
            started_at = _parse_datetime(
                payload.get("decision_completed_at"),
                fallback=event.observed_at,
            )
            try:
                outcome = evaluate_geometry_on_sip_tape(
                    trades,
                    started_at=started_at,
                    entry_reference=entry,
                    invalidation_price=invalidation,
                )
            except Exception:
                logger.debug("suppressed error in %s", "_prospective_experiment_outcomes", exc_info=True)
                continue
            records.append(
                {
                    "experiment": "ai_v1_v2_agreement",
                    "event_id": event.event_id,
                    "observed_at": event.observed_at.isoformat(),
                    "cohort": event.state,
                    "v1_action": payload.get("v1_action"),
                    "v2_state": payload.get("v2_state"),
                    "statistical_independence_claimed": False,
                    "outcome": outcome.model_dump(mode="json"),
                    "entry_clock_precision": "ai_v3_decision_completed_at",
                }
            )
    return records


def _mean_decimal(values: list[Decimal]) -> str | None:
    if not values:
        return None
    return str(sum(values, Decimal("0")) / Decimal(len(values)))


def _summarize_experiment_outcomes(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    agreement: dict[str, list[dict[str, Any]]] = {}
    geometry: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("experiment") == "ai_v1_v2_agreement":
            agreement.setdefault(str(record.get("cohort")), []).append(record)
        elif record.get("experiment") == "runner_geometry_challenger":
            key = (
                f"champion_{record.get('champion_action')}"
                f"__challenger_{record.get('challenger_action')}"
            )
            geometry.setdefault(key, []).append(record)

    def summarize(rows, outcome_key):
        outcomes = [
            row.get(outcome_key)
            for row in rows
            if isinstance(row.get(outcome_key), dict)
        ]
        mfe = [_decimal(item.get("mfe_pct")) for item in outcomes]
        mae = [_decimal(item.get("mae_pct")) for item in outcomes]
        peak = [_decimal(item.get("peak_r")) for item in outcomes]
        plus_one = [
            item.get("plus_one_r_before_minus_one_r") is True
            for item in outcomes
        ]
        plus_two = [
            item.get("plus_two_r_before_minus_one_r") is True
            for item in outcomes
        ]
        count = len(outcomes)
        return {
            "count": count,
            "mean_mfe_pct": _mean_decimal([value for value in mfe if value is not None]),
            "mean_mae_pct": _mean_decimal([value for value in mae if value is not None]),
            "mean_peak_r": _mean_decimal([value for value in peak if value is not None]),
            "plus_one_r_before_minus_one_r_rate": (
                str(Decimal(sum(plus_one)) / Decimal(count))
                if count
                else None
            ),
            "plus_two_r_before_minus_one_r_rate": (
                str(Decimal(sum(plus_two)) / Decimal(count))
                if count
                else None
            ),
        }

    return {
        "ai_v1_v2_agreement": {
            cohort: summarize(rows, "outcome")
            for cohort, rows in sorted(agreement.items())
        },
        "runner_geometry_challenger": {
            key: {
                "challenger": summarize(rows, "challenger_outcome"),
                "champion_reference": summarize(rows, "champion_outcome"),
            }
            for key, rows in sorted(geometry.items())
        },
    }


# Failures decided by the SIP tape itself. Once the session's data has settled
# a retry refetches the same tape and fails the same way, so they are final.
_DETERMINISTIC_SYMBOL_ERRORS = (
    "formal_sip_5m_session_incomplete:",
    "formal_outcome_unscorable:",
    "insufficient_eligible_regular_session_sip_trades",
    "sip_trade_events_required",
)


def _deterministic_symbol_error(exc: Exception) -> bool:
    return isinstance(exc, ValueError) and str(exc).startswith(_DETERMINISTIC_SYMBOL_ERRORS)


def _session_data_settled(session_date: date, now: datetime) -> bool:
    """SIP corrections land on the session day; the next ET day the tape is final."""
    return now.astimezone(_ET).date() > session_date


def _sip_source_ids(instrument_id: str, session_date: date) -> tuple[str, str]:
    return (
        f"alpaca_sip:trades:{instrument_id}:{session_date}",
        f"alpaca_sip:5m:{instrument_id}:{session_date}",
    )


def _prior_symbol_result(
    payload: dict[str, Any],
    *,
    instrument_id: str,
    session_date: date,
) -> tuple[dict[str, Any], tuple[EvidenceInput, ...]] | None:
    """A symbol scored by an earlier attempt of this manifest, with its SIP evidence.

    The outcome depends only on the final SIP tape and the manifest's frozen
    events, so a retry reuses it instead of refetching the whole tape.
    """
    outcomes = payload.get("outcomes")
    outcome = outcomes.get(instrument_id) if isinstance(outcomes, dict) else None
    if not isinstance(outcome, dict):
        return None
    source_ids = _sip_source_ids(instrument_id, session_date)
    found: dict[str, EvidenceInput] = {}
    for item in payload.get("reconciliation_evidence") or []:
        if isinstance(item, dict) and item.get("source_id") in source_ids:
            try:
                found[item["source_id"]] = EvidenceInput.model_validate(item)
            except Exception:
                return None
    if set(found) != set(source_ids):
        return None
    return outcome, tuple(found[source_id] for source_id in source_ids)


def _reconciliation_payload(
    manifest: SessionEvidenceManifest,
    outcomes: dict[str, Any],
    evidence: list[Any],
    errors: dict[str, str],
    deterministic_errors: set[str],
    reused: list[str],
) -> dict[str, Any]:
    experiment_records = [
        record
        for outcome in outcomes.values()
        if isinstance(outcome, dict)
        for record in (outcome.get("prospective_experiment_outcomes") or [])
        if isinstance(record, dict)
    ]
    return {
        "authority": {
            "prices": "consolidated_sip_trade_events",
            "bars": "single_provider_raw_finalized_sip_5m",
            "provider": "alpaca_sip",
            "web_fallback_allowed": False,
            "iex_fallback_allowed": False,
        },
        "expected_candidate_count": manifest.frozen_scope.expected_candidate_count,
        "scored_candidate_count": len(outcomes),
        "outcomes": dict(outcomes),
        "reconciliation_evidence": [item.model_dump(mode="json") for item in evidence],
        "errors": dict(errors),
        "deterministic_error_candidates": sorted(deterministic_errors),
        "reused_candidate_outcomes": sorted(reused),
        "experiment_summary": _summarize_experiment_outcomes(experiment_records),
    }


def _serializable_outcome(
    *,
    prices,
    measurements,
    labels,
    coverage_certificate,
) -> dict[str, Any]:
    return {
        "analysis_prices": prices.model_dump(mode="json"),
        "measurements": measurements.model_dump(mode="json"),
        "labels": labels.model_dump(mode="json"),
        "formal_bar_coverage": coverage_certificate.model_dump(mode="json"),
    }


class TradingSessionReconciliationMonitor(ScheduledTradingMonitor):
    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        manifest_repository_factory: Callable[[], SessionEvidenceRepository] = default_session_evidence_repository,
        sip_provider_factory: Callable[[], AlpacaSipResearchProvider] = AlpacaSipResearchProvider,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.manifest_repository_factory = manifest_repository_factory
        self.sip_provider_factory = sip_provider_factory
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        # One symbol's full-day SIP tape can take minutes to page through.
        self.timeout_seconds = _ATTEMPT_TIMEOUT_SECONDS
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.manifest_create_count = 0
        self.attempt_count = 0
        self.final_count = 0
        self.deferred_count = 0
        self.permanently_unscorable_count = 0

    async def _events_for_session(
        self,
        repository: TradingStrategyRepository,
        strategy_id: str,
        session_date: date,
    ):
        start, end = _session_bounds(session_date)
        events = await asyncio.to_thread(
            repository.events_between,
            strategy_id,
            start_time=start,
            end_time=end,
            limit=100_000,
        )
        if len(events) >= 100_000:
            raise RuntimeError(
                f"session_event_population_limit_reached:{strategy_id}:{session_date}"
            )
        return events, start, end

    async def _create_current_manifests(
        self,
        repository: TradingStrategyRepository,
        manifests: SessionEvidenceRepository,
        *,
        now: datetime,
    ) -> int:
        now_et = now.astimezone(_ET)
        if now_et.time() < _DEFAULT_CREATE_AFTER:
            return 0
        session_date = now_et.date()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        created = 0
        for config in configs:
            if config.config.strategy_version != "2.0.0":
                continue
            universe = await asyncio.to_thread(
                resolve_v2_evidence_archive_for_session,
                config,
                repository,
                session_date=session_date,
            )
            if universe is None:
                continue
            existing = await asyncio.to_thread(
                manifests.get_for_session,
                config.strategy_id,
                session_date,
            )
            if existing is not None:
                continue
            try:
                events, start, end = await self._events_for_session(
                    repository,
                    config.strategy_id,
                    session_date,
                )
                manifest = build_session_evidence_manifest(
                    strategy_id=config.strategy_id,
                    session_date=session_date,
                    events=events,
                    universe=universe,
                    aggregation_start=start,
                    aggregation_end=end,
                    frozen_at=now,
                )
                await asyncio.to_thread(manifests.create, manifest)
            except Exception as exc:
                self.last_error = (
                    f"manifest/{config.strategy_id}: {type(exc).__name__}: {exc}"
                )
                trade_log(
                    "auto_trading",
                    "session_evidence_manifest_error",
                    strategy_id=config.strategy_id,
                    session_date=session_date.isoformat(),
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
                continue
            created += 1
            self.manifest_create_count += 1
        return created

    async def _reconcile_symbol(
        self,
        provider: AlpacaSipResearchProvider,
        *,
        instrument_id: str,
        session_date: date,
        events: list[StrategyEvent],
    ) -> tuple[dict[str, Any], tuple[object, ...]]:
        trades = await asyncio.to_thread(
            provider.regular_session_trade_events,
            instrument_id,
            session_date,
        )
        bars = await asyncio.to_thread(
            provider.regular_session_5m_bars,
            instrument_id,
            session_date,
        )
        coverage = _complete_sip_5m_certificate(
            bars,
            trades,
            instrument_id=instrument_id,
            session_date=session_date,
        )
        if coverage.status != "VALID":
            raise ValueError(
                "formal_sip_5m_session_incomplete:"
                + ",".join(coverage.reason_codes)
            )
        prices = select_analysis_session_prices(
            trades,
            session_date=session_date,
            outcome_state="FINAL",
        )
        measurements, labels = build_formal_outcome_labels(
            prices=prices,
            bars=bars,
        )
        trade_source_id, bar_source_id = _sip_source_ids(instrument_id, session_date)
        trade_input = provider_evidence_input(
            source_id=trade_source_id,
            payload=[item.model_dump(mode="json") for item in trades],
            record_count=len(trades),
            start_time=trades[0].event_timestamp if trades else None,
            end_time=trades[-1].event_timestamp if trades else None,
        )
        bar_input = provider_evidence_input(
            source_id=bar_source_id,
            payload=[item.model_dump(mode="json") for item in bars],
            record_count=len(bars),
            start_time=bars[0].start_time if bars else None,
            end_time=bars[-1].end_time if bars else None,
        )
        outcome = _serializable_outcome(
            prices=prices,
            measurements=measurements,
            labels=labels,
            coverage_certificate=coverage,
        )
        outcome["prospective_experiment_outcomes"] = (
            _prospective_experiment_outcomes(events, trades)
        )
        return outcome, (trade_input, bar_input)

    async def _attempt_manifest(
        self,
        repository: TradingStrategyRepository,
        manifests: SessionEvidenceRepository,
        manifest: SessionEvidenceManifest,
        *,
        now: datetime,
    ) -> None:
        started = begin_reconciliation(
            manifest,
            observed_at=now,
            # Outlasts the whole cycle, so a cut-off attempt is not re-picked mid-flight.
            lease_until=now + max(
                timedelta(minutes=_retry_minutes()),
                timedelta(seconds=self.run_timeout_seconds()),
            ),
        )
        started = await asyncio.to_thread(
            manifests.save_transition,
            manifest,
            started,
        )
        self.attempt_count += 1

        try:
            config = await asyncio.to_thread(
                repository.get_config,
                started.strategy_id,
            )
            universe = await asyncio.to_thread(
                resolve_v2_evidence_archive_for_session,
                config,
                repository,
                session_date=started.frozen_scope.session_date,
            )
            if universe is None:
                raise ValueError("frozen_session_universe_unavailable")

            frozen_universe_ids = set(started.frozen_scope.universe_ids)
            if universe.universe_id not in frozen_universe_ids:
                raise ValueError(
                    "reconciliation_universe_not_in_frozen_manifest:"
                    + str(universe.universe_id)
                )

            all_events, _, _ = await self._events_for_session(
                repository,
                started.strategy_id,
                started.frozen_scope.session_date,
            )
            frozen_ids = set(started.frozen_scope.event_ids)
            frozen_events = [
                event for event in all_events if event.event_id in frozen_ids
            ]
            missing_event_ids = frozen_ids - {
                event.event_id for event in frozen_events
            }
            if missing_event_ids:
                raise ValueError(
                    "frozen_manifest_events_missing:"
                    + ",".join(sorted(missing_event_ids)[:20])
                )
            expected_event_input = next(
                (
                    item
                    for item in started.frozen_scope.evidence_inputs
                    if item.source_type == "strategy_event_stream"
                ),
                None,
            )
            actual_event_input = event_stream_input(
                started.strategy_id,
                frozen_events,
            )
            if (
                expected_event_input is None
                or expected_event_input.sha256 != actual_event_input.sha256
            ):
                raise ValueError("frozen_manifest_event_population_hash_mismatch")
            events_by_instrument: dict[str, list[StrategyEvent]] = {}
            for event in frozen_events:
                events_by_instrument.setdefault(
                    event.instrument_id,
                    [],
                ).append(event)

            provider = self.sip_provider_factory()
            session_date = started.frozen_scope.session_date
            outcomes: dict[str, Any] = {}
            evidence: list[Any] = []
            errors: dict[str, str] = {}
            deterministic_errors: set[str] = set()
            reused: list[str] = []
            # Take every earlier result up front so each checkpoint carries them all.
            pending_candidates = []
            for candidate in universe.candidates:
                prior = _prior_symbol_result(
                    started.reconciliation_payload,
                    instrument_id=candidate.instrument_id,
                    session_date=session_date,
                )
                if prior is None:
                    pending_candidates.append(candidate)
                    continue
                outcomes[candidate.instrument_id] = prior[0]
                evidence.extend(prior[1])
                reused.append(candidate.instrument_id)
            for candidate in pending_candidates:
                try:
                    outcome, inputs = await self._reconcile_symbol(
                        provider,
                        instrument_id=candidate.instrument_id,
                        session_date=started.frozen_scope.session_date,
                        events=events_by_instrument.get(
                            candidate.instrument_id,
                            [],
                        ),
                    )
                    outcomes[candidate.instrument_id] = outcome
                    evidence.extend(inputs)
                except Exception as exc:
                    errors[candidate.instrument_id] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    if _deterministic_symbol_error(exc):
                        deterministic_errors.add(candidate.instrument_id)
                    continue
                # A large SIP tape can outlast the cycle; keep what is scored.
                checkpoint = checkpoint_reconciliation(
                    started,
                    observed_at=self.now_factory(),
                    payload=_reconciliation_payload(
                        started, outcomes, evidence, errors, deterministic_errors, reused
                    ),
                )
                started = await asyncio.to_thread(
                    manifests.save_transition,
                    started,
                    checkpoint,
                )

            payload = _reconciliation_payload(
                started, outcomes, evidence, errors, deterministic_errors, reused
            )
            if not errors and len(outcomes) == len(universe.candidates):
                final = finalize_reconciliation(
                    started,
                    observed_at=now,
                    payload=payload,
                )
                await asyncio.to_thread(
                    manifests.save_transition,
                    started,
                    final,
                )
                self.final_count += 1
                trade_log(
                    "auto_trading",
                    "session_evidence_reconciliation_final",
                    strategy_id=started.strategy_id,
                    session_date=started.frozen_scope.session_date.isoformat(),
                    manifest_id=started.manifest_id,
                    scored_candidate_count=len(outcomes),
                    execution_authority=False,
                )
                return

            age = now.astimezone(_ET).date() - session_date
            # A session is scored whole or not at all; when every remaining
            # failure is fixed by the settled tape, waiting out the max age only
            # refetches the same data.
            unrecoverable = (
                bool(errors)
                and set(errors) == deterministic_errors
                and _session_data_settled(session_date, now)
            )
            if age.days >= _max_age_days() or unrecoverable:
                payload["close_reason"] = (
                    "deterministic_symbol_failures" if unrecoverable else "max_pending_age"
                )
                closed = permanently_unscorable(
                    started,
                    observed_at=now,
                    payload=payload,
                )
                await asyncio.to_thread(
                    manifests.save_transition,
                    started,
                    closed,
                )
                self.permanently_unscorable_count += 1
                trade_log(
                    "auto_trading",
                    "session_evidence_reconciliation_unscorable",
                    strategy_id=started.strategy_id,
                    session_date=session_date.isoformat(),
                    manifest_id=started.manifest_id,
                    close_reason=payload["close_reason"],
                    scored_candidate_count=len(outcomes),
                    unscorable_candidates=sorted(errors),
                    execution_authority=False,
                )
                return

            deferred = defer_reconciliation(
                started,
                observed_at=now,
                next_retry_at=now + timedelta(minutes=_retry_minutes()),
                payload=payload,
            )
            await asyncio.to_thread(
                manifests.save_transition,
                started,
                deferred,
            )
            self.deferred_count += 1
        except Exception as exc:
            self.last_error = (
                f"reconcile/{started.strategy_id}/{started.frozen_scope.session_date}: "
                f"{type(exc).__name__}: {exc}"
            )
            payload = {
                "authority": {
                    "prices": "consolidated_sip_trade_events",
                    "bars": "single_provider_raw_finalized_sip_5m",
                    "web_fallback_allowed": False,
                    "iex_fallback_allowed": False,
                },
                "errors": {
                    "__session__": f"{type(exc).__name__}: {exc}",
                },
            }
            age = now.astimezone(_ET).date() - started.frozen_scope.session_date
            if age.days >= _max_age_days():
                updated = permanently_unscorable(
                    started,
                    observed_at=now,
                    payload=payload,
                )
                self.permanently_unscorable_count += 1
            else:
                updated = defer_reconciliation(
                    started,
                    observed_at=now,
                    next_retry_at=now + timedelta(minutes=_retry_minutes()),
                    payload=payload,
                )
                self.deferred_count += 1
            await asyncio.to_thread(
                manifests.save_transition,
                started,
                updated,
            )

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("session reconciliation clock must be timezone-aware")
        repository = self.strategy_repository_factory()
        manifests = self.manifest_repository_factory()

        await self._create_current_manifests(
            repository,
            manifests,
            now=now,
        )
        pending = await asyncio.to_thread(
            manifests.pending,
            as_of=now,
            limit=20,
        )
        for manifest in pending:
            try:
                await self._attempt_manifest(
                    repository,
                    manifests,
                    manifest,
                    now=now,
                )
            except Exception as exc:
                self.last_error = (
                    f"{manifest.manifest_id}: {type(exc).__name__}: {exc}"
                )
        self.last_run_at = now
        return len(pending)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": session_reconciliation_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "retry_minutes": _retry_minutes(),
            "maximum_pending_age_days": _max_age_days(),
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "manifest_create_count": self.manifest_create_count,
            "attempt_count": self.attempt_count,
            "final_count": self.final_count,
            "deferred_count": self.deferred_count,
            "permanently_unscorable_count": self.permanently_unscorable_count,
            "authoritative_price_source": "consolidated_sip_trade_events",
            "authoritative_bar_source": "single_provider_raw_finalized_sip_5m",
            "fallback_substitution_allowed": False,
        }


def create_trading_session_reconciliation_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingSessionReconciliationMonitor):
        return None
    monitor = TradingSessionReconciliationMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=session_reconciliation_monitor_enabled)


__all__ = [
    "TradingSessionReconciliationMonitor",
    "create_trading_session_reconciliation_monitor_task",
    "session_reconciliation_monitor_enabled",
]
