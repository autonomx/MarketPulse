from __future__ import annotations

from app.apps.trading.us_equity_calendar import after_regular_close

import logging

logger = logging.getLogger(__name__)

"""Roadmap-completeness policy for the AI Shadow v2 experiment.

This layer owns experimental validity rather than execution authority. It is
installed after ``strategy_ai_shadow_v2_hardening`` and closes the remaining
roadmap gaps:

* catalyst/control pairs are evaluated on the same causal observation schedule;
* the morning catalyst prior is only frozen from research known before entry;
* catalyst evidence is filtered causally and primary verification is tightened;
* alpha receives multi-timeframe + cohort-relative context, never execution state;
* the catalyst confirmation hurdle is enforced as an alpha-policy boundary;
* provider failures are isolated with a bounded retry checkpoint;
* every decision, including ``avoid``, receives a post-close counterfactual label;
* empirical calibration uses one versioned canonical treatment arm, avoiding
  duplicate control/treatment samples and stale-policy mixing.

The module remains research-only. It does not create real-money order authority.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from . import strategy_ai_shadow_v2_hardening as hardening
from . import strategy_ai_shadow_v2_monitor as monitor
from .research.contracts import TradingEvidence
from .strategy_ai_shadow_v2 import (
    AI_SHADOW_V2_POLICY_VERSION,
    AIShadowV2AlphaDecision,
    CatalystIntelligenceSnapshot,
)
from .strategy_repository import StrategyEvent

_ALPHA_ERROR_COOLDOWN = timedelta(seconds=60)
_OUTCOME_HORIZON = timedelta(minutes=60)
_SUPPLY_ONLY_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "424B3", "424B5", "RW", "EFFECT"}

def _known_by(item: TradingEvidence, as_of: datetime) -> bool:
    cutoff = as_of.astimezone(timezone.utc)
    known = item.omnix_known_at or item.captured_at
    if known.astimezone(timezone.utc) > cutoff:
        return False
    for value in (item.source_available_at, item.source_published_at):
        if value is not None and value.astimezone(timezone.utc) > cutoff:
            return False
    return True


def _causal_evidence(
    evidence: list[TradingEvidence] | tuple[TradingEvidence, ...],
    *,
    as_of: datetime,
) -> list[TradingEvidence]:
    return [item for item in evidence if _known_by(item, as_of)]


def _recent_primary_catalyst_evidence(
    evidence: list[TradingEvidence] | tuple[TradingEvidence, ...],
    *,
    as_of: datetime,
) -> list[TradingEvidence]:
    """Return recent primary evidence plausibly capable of confirming the gap reason."""

    cutoff = as_of.astimezone(timezone.utc)
    lower = cutoff - timedelta(hours=72)
    result: list[TradingEvidence] = []
    for item in _causal_evidence(evidence, as_of=cutoff):
        if item.source_authority_tier != 1:
            continue
        published = item.source_published_at or item.source_available_at or item.captured_at
        published = published.astimezone(timezone.utc)
        if published < lower:
            continue
        if item.source_type == "company_ir":
            result.append(item)
            continue
        if item.source_type != "sec":
            continue
        form = str(item.metadata.get("form") or "").upper()
        if form not in _SUPPLY_ONLY_FORMS:
            result.append(item)
    return result


def _stable_snapshot_id(instrument_id: str, evidence_fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{instrument_id}|{evidence_fingerprint}".encode("utf-8")
    ).hexdigest()[:24]
    return f"catalyst-v2-{digest}"


def _catalyst_assess_policy(
    self,
    *,
    instrument_id: str,
    as_of: datetime,
    report,
    evidence,
    morning_context=None,
    empirical_persistence_rate=None,
    empirical_sample_size: int = 0,
    original,
):
    causal = _causal_evidence(evidence, as_of=as_of)
    snapshot = original(
        self,
        instrument_id=instrument_id,
        as_of=as_of,
        report=report,
        evidence=causal,
        morning_context=morning_context,
        empirical_persistence_rate=empirical_persistence_rate,
        empirical_sample_size=empirical_sample_size,
    )
    primary_candidates = _recent_primary_catalyst_evidence(causal, as_of=as_of)
    report_confirms = report is None or getattr(report, "catalyst_status", None) == "confirmed"
    verified = bool(primary_candidates) and report_confirms
    influence = monitor.derive_catalyst_influence(
        persistence_class=snapshot.intraday_persistence_class,
        primary_source_verified=verified,
        supply_pressure=snapshot.supply_pressure,
        promotional_risk=snapshot.promotional_risk,
        gap_already_prices_in_news=snapshot.gap_already_prices_in_news,
        empirical_persistence_rate=snapshot.influence.empirical_persistence_rate,
        empirical_sample_size=snapshot.influence.empirical_sample_size,
    )
    return snapshot.model_copy(
        update={
            "snapshot_id": _stable_snapshot_id(instrument_id, snapshot.evidence_fingerprint),
            "primary_source_verified": verified,
            "influence": influence,
        }
    )


def _morning_freeze_allowed(now: datetime, config) -> bool:
    return now.astimezone(monitor._ET).time() <= config.risk.entry_start_et


def _report_is_current_session(report, now: datetime) -> bool:
    known = getattr(report, "omnix_known_at", None) or getattr(report, "research_completed_at", None)
    return bool(known and known.astimezone(monitor._ET).date() == now.astimezone(monitor._ET).date())


async def _refresh_catalyst_policy(
    self,
    *,
    candidate,
    config,
    strategy_repository,
    research_repository,
    events,
    now,
    history,
    original,
):
    snapshot = await original(
        candidate=candidate,
        config=config,
        strategy_repository=strategy_repository,
        research_repository=research_repository,
        events=events,
        now=now,
        history=history,
    )

    if (
        hardening._morning_snapshot(events, candidate.instrument_id) is None
        and _morning_freeze_allowed(now, config)
    ):
        report = await monitor.asyncio.to_thread(
            research_repository.latest_report_as_of,
            candidate.instrument_id,
            now,
        )
        if report is not None and _report_is_current_session(report, now):
            session_date = now.astimezone(monitor._ET).date().isoformat()
            await self._append(
                strategy_repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type="ai_v2_catalyst_freeze",
                state=snapshot.intraday_persistence_class,
                reason_code="AI_V2_MORNING_CATALYST_FROZEN",
                observed_at=now,
                payload={
                    "snapshot": snapshot.model_dump(mode="json"),
                    "snapshot_mode": "morning_frozen",
                    "session_date": session_date,
                    "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                    "research_only": True,
                    "execution_authority": False,
                },
                identity=(
                    candidate.instrument_id,
                    session_date,
                    AI_SHADOW_V2_POLICY_VERSION,
                    "morning-catalyst-freeze",
                ),
            )
    return snapshot


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _pct(new: Decimal, old: Decimal) -> Decimal | None:
    if old <= 0:
        return None
    return (new / old - Decimal("1")) * Decimal("100")


def _aggregate_bars(bars: list[Any], minutes: int) -> list[dict[str, Any]]:
    regular = sorted(
        [
            bar
            for bar in bars
            if getattr(bar, "is_final", False)
            and getattr(bar, "session", None) == "regular"
        ],
        key=lambda bar: bar.end_time,
    )
    buckets: dict[int, list[Any]] = {}
    width = minutes * 60
    for bar in regular:
        key = int(bar.end_time.timestamp() // width)
        buckets.setdefault(key, []).append(bar)
    result: list[dict[str, Any]] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda bar: bar.end_time)
        first, last = group[0], group[-1]
        result.append(
            {
                "start": first.start_time,
                "end": last.end_time,
                "open": Decimal(first.open),
                "high": max(Decimal(bar.high) for bar in group),
                "low": min(Decimal(bar.low) for bar in group),
                "close": Decimal(last.close),
                "volume": sum((Decimal(bar.volume) for bar in group), Decimal("0")),
            }
        )
    return result


def _slope_pct_per_bar(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2 or values[0] <= 0:
        return None
    return ((values[-1] / values[0] - Decimal("1")) * Decimal("100")) / Decimal(
        len(values) - 1
    )


def _multi_timeframe_context(row: dict[str, Any]) -> dict[str, Any]:
    bars = row.get("bars")
    structure = row.get("structure")
    if not isinstance(bars, list) or not isinstance(structure, monitor.MarketStructureSnapshot):
        return {}

    regular = sorted(
        [
            bar
            for bar in bars
            if getattr(bar, "is_final", False)
            and getattr(bar, "session", None) == "regular"
            and bar.end_time <= structure.observed_at
        ],
        key=lambda bar: bar.end_time,
    )
    if not regular:
        return {}

    current = Decimal(structure.current_price)
    ema9_distance = _pct(current, Decimal(structure.ema9_1m)) if structure.ema9_1m is not None else None
    ema20_distance = _pct(current, Decimal(structure.ema20_1m)) if structure.ema20_1m is not None else None

    true_ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for bar in regular:
        high, low, close = Decimal(bar.high), Decimal(bar.low), Decimal(bar.close)
        true_range = high - low if previous_close is None else max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        true_ranges.append(true_range)
        previous_close = close
    atr14 = (
        sum(true_ranges[-14:], Decimal("0")) / Decimal(min(14, len(true_ranges)))
        if true_ranges
        else None
    )
    atr14_pct = atr14 / current * Decimal("100") if atr14 is not None and current > 0 else None

    recent10 = regular[-10:]
    ranges = [
        (Decimal(bar.high) - Decimal(bar.low)) / Decimal(bar.close) * Decimal("100")
        for bar in recent10
        if Decimal(bar.close) > 0
    ]
    average_range_pct_10 = sum(ranges, Decimal("0")) / Decimal(len(ranges)) if ranges else None

    last3 = regular[-3:]
    prior10 = regular[-13:-3]
    short_volume = sum((Decimal(bar.volume) for bar in last3), Decimal("0")) / Decimal(len(last3)) if last3 else None
    prior_volume = sum((Decimal(bar.volume) for bar in prior10), Decimal("0")) / Decimal(len(prior10)) if prior10 else None
    volume_contraction_ratio = (
        short_volume / prior_volume
        if short_volume is not None and prior_volume is not None and prior_volume > 0
        else None
    )

    three = _aggregate_bars(regular, 3)
    five = _aggregate_bars(regular, 5)
    three_slope = _slope_pct_per_bar([item["close"] for item in three[-6:]])
    five_slope = _slope_pct_per_bar([item["close"] for item in five[-6:]])

    session_open = Decimal(regular[0].open)
    compressed: list[dict[str, Any]] = []
    for item in five[-24:]:
        return_from_open = _pct(item["close"], session_open)
        compressed.append(
            {
                "end": item["end"].isoformat(),
                "open": str(item["open"]),
                "high": str(item["high"]),
                "low": str(item["low"]),
                "close": str(item["close"]),
                "volume": str(item["volume"]),
                "return_from_session_open_pct": str(return_from_open) if return_from_open is not None else None,
            }
        )

    return {
        "ema9_distance_pct": str(ema9_distance) if ema9_distance is not None else None,
        "ema20_distance_pct": str(ema20_distance) if ema20_distance is not None else None,
        "atr14_pct": str(atr14_pct) if atr14_pct is not None else None,
        "average_range_pct_10": str(average_range_pct_10) if average_range_pct_10 is not None else None,
        "short_volume_to_prior10_ratio": str(volume_contraction_ratio) if volume_contraction_ratio is not None else None,
        "three_minute_trend_slope_pct_per_bar": str(three_slope) if three_slope is not None else None,
        "five_minute_trend_slope_pct_per_bar": str(five_slope) if five_slope is not None else None,
        "compressed_5m_path_last_120m": compressed,
    }


def _cohort_context(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values: list[tuple[str, Decimal, Decimal | None]] = []
    for row in rows:
        candidate = row.get("candidate")
        structure = row.get("structure")
        if candidate is None or not isinstance(structure, monitor.MarketStructureSnapshot):
            continue
        values.append(
            (
                candidate.instrument_id,
                Decimal(structure.session_return_pct),
                Decimal(structure.five_minute_return_pct) if structure.five_minute_return_pct is not None else None,
            )
        )
    returns = [item[1] for item in values]
    five_returns = [item[2] for item in values if item[2] is not None]
    median_return = _median(returns)
    median_five = _median(five_returns)
    ranked = sorted(values, key=lambda item: (-item[1], item[0]))
    rank_by_id = {item[0]: index for index, item in enumerate(ranked, start=1)}
    result: dict[str, dict[str, Any]] = {}
    for instrument_id, session_return, five_return in values:
        result[instrument_id] = {
            "cohort_size": len(values),
            "session_return_rank": rank_by_id[instrument_id],
            "session_return_vs_cohort_median_pct_points": str(session_return - median_return) if median_return is not None else None,
            "five_minute_return_vs_cohort_median_pct_points": (
                str(five_return - median_five)
                if five_return is not None and median_five is not None
                else None
            ),
        }
    return result


def _paired_arm(arm: str) -> str | None:
    return {
        "morning_control": "morning_catalyst",
        "morning_catalyst": "morning_control",
        "full_session_control": "full_session_catalyst",
        "full_session_catalyst": "full_session_control",
    }.get(arm)


def _previous_trigger_satisfied(previous: StrategyEvent | None, *, structure: monitor.MarketStructureSnapshot) -> bool:
    if previous is None or previous.payload.get("effective_state") != "armed":
        return False
    decision = previous.payload.get("decision")
    if not isinstance(decision, dict):
        return False
    try:
        trigger = monitor.StructuredAlphaTrigger.model_validate(decision.get("trigger"))
    except Exception:
        return False
    structure_at = structure.observed_at.astimezone(previous.observed_at.tzinfo)
    if structure_at > previous.observed_at + timedelta(minutes=trigger.expiry_minutes):
        return False
    prior = None
    feature = previous.payload.get("feature_snapshot")
    if isinstance(feature, dict) and isinstance(feature.get("market_structure"), dict):
        try:
            prior = monitor.MarketStructureSnapshot.model_validate(feature["market_structure"])
        except Exception:
            prior = None
    return monitor.trigger_satisfied(trigger, structure=structure, previous_structure=prior)


def _latest_alpha_error(events: list[StrategyEvent], *, arm: str, instrument_id: str) -> StrategyEvent | None:
    values = [
        event
        for event in events
        if event.event_type == "ai_v2_alpha_error"
        and event.instrument_id == instrument_id
        and event.payload.get("arm") == arm
    ]
    return max(values, key=lambda event: (event.observed_at, event.event_id)) if values else None


def _pair_has_recent_error(
    events: list[StrategyEvent],
    *,
    arms: tuple[str, ...],
    instrument_id: str,
    at: datetime,
) -> bool:
    errors = []
    for arm in arms:
        value = _latest_alpha_error(events, arm=arm, instrument_id=instrument_id)
        if value is not None:
            errors.append(value)
    if not errors:
        return False
    latest = max(errors, key=lambda event: event.observed_at)
    return at - latest.observed_at.astimezone(at.tzinfo) < _ALPHA_ERROR_COOLDOWN


def _due_reasons(
    events: list[StrategyEvent],
    *,
    arm: str,
    paired_arm: str | None,
    instrument_id: str,
    at: datetime,
    structure: monitor.MarketStructureSnapshot,
    current_catalyst_fingerprint: str | None = None,
) -> tuple[str, ...]:
    own = monitor._previous_decision(events, cast(Any, arm), instrument_id)
    pair = monitor._previous_decision(events, cast(Any, paired_arm), instrument_id) if paired_arm is not None else None
    previous = [item for item in (own, pair) if item is not None]
    reasons: list[str] = []

    if own is None or (paired_arm is not None and pair is None):
        reasons.append("paired_initial" if paired_arm is not None else "initial")
    if any(_previous_trigger_satisfied(item, structure=structure) for item in previous):
        reasons.append("paired_armed_trigger_satisfied" if paired_arm is not None else "armed_trigger_satisfied")
    if previous:
        oldest = min(item.observed_at.astimezone(at.tzinfo) for item in previous)
        if at - oldest >= timedelta(minutes=5):
            reasons.append("paired_five_minute_heartbeat" if paired_arm is not None else "five_minute_heartbeat")

    if arm.startswith("full_session_") and paired_arm is not None:
        catalyst_decision = monitor._previous_decision(
            events,
            "full_session_catalyst",
            instrument_id,
        )
        feature = (
            catalyst_decision.payload.get("feature_snapshot")
            if catalyst_decision is not None
            else None
        )
        catalyst = (
            feature.get("catalyst_intelligence")
            if isinstance(feature, dict)
            else None
        )
        previous_fingerprint = (
            str(catalyst.get("evidence_fingerprint"))
            if isinstance(catalyst, dict) and catalyst.get("evidence_fingerprint")
            else None
        )
        if (
            current_catalyst_fingerprint is not None
            and previous_fingerprint is not None
            and current_catalyst_fingerprint != previous_fingerprint
        ):
            reasons.append("paired_catalyst_evidence_changed")

    if reasons and _pair_has_recent_error(
        events,
        arms=tuple(item for item in (arm, paired_arm) if item is not None),
        instrument_id=instrument_id,
        at=at,
    ):
        return ()
    return tuple(dict.fromkeys(reasons))


def _decision_at(row: dict[str, Any], *, arm: str, events: list[StrategyEvent]) -> datetime:
    structure = row["structure"]
    assert isinstance(structure, monitor.MarketStructureSnapshot)
    value = structure.observed_at.astimezone(timezone.utc)
    candidate = row["candidate"]
    snapshot: CatalystIntelligenceSnapshot | None = None
    if arm.startswith("morning_"):
        snapshot = hardening._morning_snapshot(events, candidate.instrument_id)
    else:
        current = row.get("catalyst")
        if isinstance(current, CatalystIntelligenceSnapshot):
            snapshot = current
    if snapshot is not None:
        value = max(value, snapshot.as_of.astimezone(timezone.utc))
    return value


def _enforce_confirmation_hurdle(
    decision: AIShadowV2AlphaDecision,
    *,
    feature: dict[str, Any],
) -> tuple[AIShadowV2AlphaDecision, str | None]:
    if decision.state not in {"armed", "enter"}:
        return decision, None
    hurdle = feature.get("alpha_confirmation_hurdle")
    market = feature.get("market_structure")
    if not isinstance(market, dict) or hurdle is None:
        return decision, None
    confirmation = market.get("confirmation_score")
    if confirmation is None:
        return decision, None
    try:
        confirmation_value = int(confirmation)
        hurdle_value = int(hurdle)
    except (TypeError, ValueError):
        return decision, None
    if confirmation_value >= hurdle_value:
        return decision, None
    evidence_against = tuple(
        [
            *decision.evidence_against,
            f"market confirmation {confirmation_value} below hurdle {hurdle_value}",
        ][-8:]
    )
    return (
        decision.model_copy(
            update={
                "state": "watch",
                "trigger": None,
                "evidence_against": evidence_against,
                "thesis_changed": True,
            }
        ),
        "AI_V2_ALPHA_CONFIRMATION_HURDLE_NOT_MET",
    )


async def _run_arm_policy(
    self,
    *,
    arm,
    rows,
    config,
    repository,
    events,
):
    prepared: list[dict[str, Any]] = []
    reasons_by_id: dict[str, tuple[str, ...]] = {}
    observed_spreads: dict[str, Decimal] = {}
    cohort = _cohort_context(rows)
    paired = _paired_arm(arm)

    for source in rows:
        row = dict(source)
        candidate = row["candidate"]
        instrument_id = candidate.instrument_id
        feature_by_arm = dict(row.get("feature_by_arm") or {})
        feature = dict(feature_by_arm.get(arm) or {})

        microstructure = feature.get("market_microstructure")
        if isinstance(microstructure, dict) and microstructure.get("spread_bps") is not None:
            try:
                spread = Decimal(str(microstructure["spread_bps"]))
                if spread >= 0:
                    observed_spreads[instrument_id] = spread
            except Exception:
                logger.debug("suppressed error in %s", "_run_arm_policy", exc_info=True)

        frozen = hardening._morning_snapshot(events, instrument_id)
        if arm == "morning_catalyst":
            if frozen is None:
                feature.pop("catalyst_intelligence", None)
                feature.pop("empirical_setup_calibration", None)
                feature["alpha_confirmation_hurdle"] = 60
                feature["catalyst_snapshot_mode"] = "morning_unavailable"
                feature["experiment_policy_version"] = AI_SHADOW_V2_POLICY_VERSION
                await self._append(
                    repository,
                    config,
                    instrument_id=instrument_id,
                    event_type="ai_v2_arm_unavailable",
                    state="unavailable",
                    reason_code="AI_V2_MORNING_CATALYST_NOT_FROZEN",
                    observed_at=_decision_at(row, arm=arm, events=events),
                    payload={
                        "arm": arm,
                        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                        "reason": "No catalyst snapshot was causally frozen before the entry window.",
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(arm, instrument_id, config.strategy_id, "morning-unavailable"),
                )
                continue
            feature = hardening._sanitized_alpha_feature(feature, frozen_catalyst=frozen)
        else:
            feature = hardening._sanitized_alpha_feature(feature)
            if arm == "full_session_catalyst":
                feature["catalyst_snapshot_mode"] = "event_refreshed"

        feature["multi_timeframe_context"] = _multi_timeframe_context(row)
        feature["cohort_context"] = cohort.get(instrument_id, {})
        feature["experiment_policy_version"] = AI_SHADOW_V2_POLICY_VERSION
        feature_by_arm[arm] = feature
        row["feature_by_arm"] = feature_by_arm

        at = _decision_at(row, arm=arm, events=events)
        row["observed_at"] = at

        position = monitor._position(events, arm, instrument_id)
        stop = hardening._active_stop_price(events, arm=arm, instrument_id=instrument_id)
        if position.is_long and stop is not None and hardening._stop_was_breached(
            row, stop=stop, entry_time=position.entry_time
        ):
            decision = AIShadowV2AlphaDecision(
                instrument_id=instrument_id,
                setup_family="unresolved",
                state="exit",
                quality_score=100,
                extension_risk="high",
                evidence_against=("deterministic invalidation breached",),
                thesis_changed=True,
                thesis="Deterministic risk boundary breached; alpha cannot override the stop.",
            )
            await self._apply_decision(
                arm=arm,
                decision=decision,
                row=row,
                config=config,
                repository=repository,
                events=events,
                trigger_reasons=("deterministic_stop_breached",),
            )
            continue

        if not position.is_long and at.astimezone(monitor._ET).time() > monitor._entry_end(config, arm):
            continue
        if at.astimezone(monitor._ET).time() >= config.risk.force_flat_et:
            continue
        if monitor._decision_exists(events, arm, instrument_id, at):
            continue

        pair_for_row = paired
        if arm.startswith("morning_") and frozen is None:
            pair_for_row = None
        reasons = _due_reasons(
            events,
            arm=arm,
            paired_arm=pair_for_row,
            instrument_id=instrument_id,
            at=at,
            structure=row["structure"],
            current_catalyst_fingerprint=getattr(
                row.get("catalyst"),
                "evidence_fingerprint",
                None,
            ),
        )
        if not reasons:
            continue
        prepared.append(row)
        reasons_by_id[instrument_id] = reasons

    if not prepared:
        return None

    analyzer_rows = []
    for row in prepared:
        candidate = row["candidate"]
        previous = monitor._previous_decision(events, arm, candidate.instrument_id)
        previous_decision = (
            previous.payload.get("decision")
            if previous and isinstance(previous.payload.get("decision"), dict)
            else None
        )
        analyzer_rows.append(
            {
                "instrument_id": candidate.instrument_id,
                "observed_at": row["observed_at"].isoformat(),
                "feature_snapshot": row["feature_by_arm"][arm],
                "previous_alpha_context": {
                    "state": previous_decision.get("state") if previous_decision else None,
                    "setup_family": previous_decision.get("setup_family") if previous_decision else None,
                    "thesis": previous_decision.get("thesis") if previous_decision else None,
                    "trigger": previous_decision.get("trigger") if previous_decision else None,
                },
                "trigger_reasons": list(reasons_by_id[candidate.instrument_id]),
            }
        )

    token = hardening._OBSERVED_SPREAD_BPS.set(observed_spreads)
    self.alpha_call_count += 1
    try:
        analyzer = self.alpha_analyzer_factory()
        try:
            decisions = await monitor.asyncio.to_thread(analyzer.assess, arm=arm, rows=analyzer_rows)
        except Exception as exc:
            self.last_error = f"{config.strategy_id}/{arm}: {type(exc).__name__}: {exc}"
            for row in prepared:
                candidate = row["candidate"]
                await self._append(
                    repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type="ai_v2_alpha_error",
                    state="error",
                    reason_code="AI_V2_ALPHA_PROVIDER_ERROR",
                    observed_at=row["observed_at"],
                    payload={
                        "arm": arm,
                        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:1000],
                        "trigger_reasons": list(reasons_by_id[candidate.instrument_id]),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(arm, row["observed_at"].isoformat(), AI_SHADOW_V2_POLICY_VERSION, "alpha-error"),
                )
            monitor.trade_log(
                "auto_trading",
                "ai_shadow_v2_alpha_error",
                strategy_id=config.strategy_id,
                arm=arm,
                error_type=type(exc).__name__,
                detail=str(exc),
                execution_authority=False,
            )
            return None

        by_id = {row["candidate"].instrument_id: row for row in prepared}
        for raw_decision in decisions:
            found_row = by_id.get(raw_decision.instrument_id)
            if found_row is None:
                continue
            row = found_row
            feature = row["feature_by_arm"][arm]
            decision, veto = _enforce_confirmation_hurdle(raw_decision, feature=feature)
            if veto is not None:
                market = feature.get("market_structure")
                await self._append(
                    repository,
                    config,
                    instrument_id=raw_decision.instrument_id,
                    event_type="ai_v2_alpha_policy_veto",
                    state="watch",
                    reason_code=veto,
                    observed_at=row["observed_at"],
                    payload={
                        "arm": arm,
                        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                        "raw_decision": raw_decision.model_dump(mode="json"),
                        "confirmation_score": market.get("confirmation_score") if isinstance(market, dict) else None,
                        "confirmation_hurdle": feature.get("alpha_confirmation_hurdle"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(arm, row["observed_at"].isoformat(), AI_SHADOW_V2_POLICY_VERSION, "alpha-policy-veto"),
                )
            await self._apply_decision(
                arm=arm,
                decision=decision,
                row=row,
                config=config,
                repository=repository,
                events=events,
                trigger_reasons=reasons_by_id.get(raw_decision.instrument_id, ()),
            )
    finally:
        hardening._OBSERVED_SPREAD_BPS.reset(token)
    return None


def _decision_groups(decisions: list[StrategyEvent]) -> list[tuple[str, list[StrategyEvent]]]:
    groups: list[tuple[str, list[StrategyEvent]]] = []
    current_kind: str | None = None
    current: list[StrategyEvent] = []
    for event in sorted(decisions, key=lambda item: item.observed_at):
        state = str(event.payload.get("effective_state") or "")
        if state in {"watch", "armed", "enter", "manage"}:
            kind = "active"
        elif state == "avoid":
            kind = "avoid"
        else:
            kind = "boundary"
        if kind == "boundary":
            if current:
                groups.append((current_kind or "active", current))
                current = []
                current_kind = None
            continue
        if current and kind != current_kind:
            groups.append((current_kind or "active", current))
            current = []
        current_kind = kind
        current.append(event)
    if current:
        groups.append((current_kind or "active", current))
    return groups


def _outcome_end(reference_at: datetime, bars: list[Any]) -> datetime:
    return min(max(bar.end_time for bar in bars), reference_at + _OUTCOME_HORIZON)


def _event_outcome(
    *,
    arm: str,
    instrument_id: str,
    identifier: str,
    event: StrategyEvent,
    bars: list[Any],
    entered: bool,
    persistence,
):
    decision = event.payload.get("decision")
    feature = event.payload.get("feature_snapshot")
    if not isinstance(decision, dict) or not isinstance(feature, dict):
        return None
    market = feature.get("market_structure")
    if not isinstance(market, dict):
        return None
    structure = monitor.MarketStructureSnapshot.model_validate(market)
    invalidation = Decimal(str(decision["invalidation_price"])) if decision.get("invalidation_price") is not None else None
    target = Decimal(str(decision["target_1"])) if decision.get("target_1") is not None else None
    return monitor.evaluate_opportunity_episode(
        arm=cast(Any, arm),
        instrument_id=instrument_id,
        episode_id=identifier,
        setup_family=cast(Any, str(decision.get("setup_family") or "unresolved")),
        started_at=event.observed_at,
        ended_at=_outcome_end(event.observed_at, bars),
        entry_price=structure.current_price,
        invalidation_price=invalidation,
        target_1=target,
        bars=bars,
        entered=entered,
        catalyst_persistence_class=persistence,
    )


async def _label_episodes_policy(
    self,
    *,
    rows,
    config,
    repository,
    events,
    now,
):
    if not after_regular_close(now):
        return

    existing_episodes = {
        str(event.payload.get("episode_id"))
        for event in events
        if event.event_type == "ai_v2_opportunity_episode"
    }
    existing_decisions = {
        str(event.payload.get("decision_event_id"))
        for event in events
        if event.event_type == "ai_v2_decision_outcome"
    }
    rows_by_id = {row["candidate"].instrument_id: row for row in rows}

    for arm in monitor._ARMS:
        for instrument_id, row in rows_by_id.items():
            bars = row.get("bars")
            if not isinstance(bars, list) or not bars:
                continue
            decisions = [
                event
                for event in events
                if event.event_type == "ai_v2_decision"
                and event.instrument_id == instrument_id
                and event.payload.get("arm") == arm
                and isinstance(event.payload.get("feature_snapshot"), dict)
                and event.payload["feature_snapshot"].get("experiment_policy_version") == AI_SHADOW_V2_POLICY_VERSION
            ]

            for kind, group in _decision_groups(decisions):
                first = group[0]
                reference = hardening._episode_reference_event(group) if kind == "active" else first
                episode_id = monitor._key(
                    AI_SHADOW_V2_POLICY_VERSION,
                    arm,
                    instrument_id,
                    kind,
                    first.observed_at.isoformat(),
                )[:28]
                if episode_id in existing_episodes:
                    continue
                feature = reference.payload.get("feature_snapshot")
                persistence = None
                if isinstance(feature, dict) and monitor._is_catalyst(arm):
                    catalyst_payload = feature.get("catalyst_intelligence")
                    if isinstance(catalyst_payload, dict):
                        persistence = catalyst_payload.get("intraday_persistence_class")
                try:
                    outcome = _event_outcome(
                        arm=arm,
                        instrument_id=instrument_id,
                        identifier=episode_id,
                        event=reference,
                        bars=bars,
                        entered=any(event.payload.get("effective_state") == "enter" for event in group),
                        persistence=persistence,
                    )
                except Exception:
                    outcome = None
                if outcome is None:
                    continue
                if await self._append(
                    repository,
                    config,
                    instrument_id=instrument_id,
                    event_type="ai_v2_opportunity_episode",
                    state="positive" if outcome.positive_opportunity else "negative",
                    reason_code="AI_V2_OPPORTUNITY_EPISODE",
                    observed_at=now,
                    payload={
                        "version": monitor.AI_SHADOW_V2_VERSION,
                        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                        "arm": arm,
                        "episode_kind": kind,
                        "episode_id": episode_id,
                        "reference_state": reference.payload.get("effective_state"),
                        "reference_observed_at": reference.observed_at.isoformat(),
                        "outcome": outcome.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(arm, episode_id, AI_SHADOW_V2_POLICY_VERSION),
                ):
                    self.episode_count += 1

            for decision_event in decisions:
                if decision_event.event_id in existing_decisions:
                    continue
                feature = decision_event.payload.get("feature_snapshot")
                persistence = None
                if isinstance(feature, dict) and monitor._is_catalyst(arm):
                    catalyst_payload = feature.get("catalyst_intelligence")
                    if isinstance(catalyst_payload, dict):
                        persistence = catalyst_payload.get("intraday_persistence_class")
                identifier = monitor._key(
                    AI_SHADOW_V2_POLICY_VERSION,
                    "decision",
                    decision_event.event_id,
                )[:28]
                try:
                    outcome = _event_outcome(
                        arm=arm,
                        instrument_id=instrument_id,
                        identifier=identifier,
                        event=decision_event,
                        bars=bars,
                        entered=decision_event.payload.get("effective_state") == "enter",
                        persistence=persistence,
                    )
                except Exception:
                    outcome = None
                if outcome is None:
                    continue
                await self._append(
                    repository,
                    config,
                    instrument_id=instrument_id,
                    event_type="ai_v2_decision_outcome",
                    state="positive" if outcome.positive_opportunity else "negative",
                    reason_code="AI_V2_DECISION_COUNTERFACTUAL",
                    observed_at=now,
                    payload={
                        "version": monitor.AI_SHADOW_V2_VERSION,
                        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
                        "arm": arm,
                        "decision_event_id": decision_event.event_id,
                        "alpha_state": decision_event.payload.get("alpha_state"),
                        "effective_state": decision_event.payload.get("effective_state"),
                        "outcome": outcome.model_dump(mode="json"),
                        "research_only": True,
                        "execution_authority": False,
                    },
                    identity=(arm, decision_event.event_id, AI_SHADOW_V2_POLICY_VERSION, "decision-outcome"),
                )


def _historical_episodes_policy(repository, strategy_id: str) -> list[dict[str, Any]]:
    from .strategy_outcome_quality import outcome_is_valid_compat

    try:
        recent = repository.recent_events(strategy_id, 50_000)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in recent:
        if (
            event.event_type != "ai_v2_opportunity_episode"
            or event.payload.get("arm") != "full_session_catalyst"
            or event.payload.get("policy_version") != AI_SHADOW_V2_POLICY_VERSION
            or not isinstance(event.payload.get("outcome"), dict)
            or not outcome_is_valid_compat(event.payload["outcome"])
        ):
            continue
        episode_id = str(event.payload.get("episode_id") or event.event_id)
        if episode_id in seen:
            continue
        seen.add(episode_id)
        rows.append(dict(event.payload["outcome"]))
    return rows


def _persistence_calibration_policy(
    episodes: list[dict[str, Any]],
    persistence: str,
) -> tuple[Decimal | None, int]:
    values = [
        row
        for row in episodes
        if row.get("catalyst_persistence_class") == persistence
        and row.get("plus_two_r_before_minus_one_r") is not None
    ]
    if len(values) < 100:
        return None, len(values)
    wins = sum(row.get("plus_two_r_before_minus_one_r") is True for row in values)
    return Decimal(wins) / Decimal(len(values)), len(values)


def _setup_calibration_policy(
    episodes: list[dict[str, Any]],
    persistence: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for setup in (
        "trend_continuation",
        "failed_selloff_reclaim",
        "first_pullback",
        "squeeze_continuation",
        "gap_hold",
        "distribution",
        "unresolved",
    ):
        values = [
            row
            for row in episodes
            if row.get("catalyst_persistence_class") == persistence
            and row.get("setup_family") == setup
            and row.get("plus_two_r_before_minus_one_r") is not None
        ]
        wins = sum(row.get("plus_two_r_before_minus_one_r") is True for row in values)
        result[setup] = {
            "sample_size": len(values),
            "two_r_before_minus_one_r_rate": str(Decimal(wins) / Decimal(len(values))) if len(values) >= 100 else None,
            "calibrated": len(values) >= 100,
            "minimum_sample": 100,
        }
    return result


def _decision_outcome_metrics(events: list[StrategyEvent], arm: str) -> dict[str, Any]:
    from .strategy_outcome_quality import outcome_is_valid_compat

    paired_arm = _paired_arm(arm)
    paired_decision_ids: set[str] | None = None
    if paired_arm is not None:
        def decision_keys(decision_arm: str) -> dict[tuple[str, str], set[str]]:
            result: dict[tuple[str, str], set[str]] = {}
            for event in events:
                if event.event_type != "ai_v2_decision" or event.payload.get("arm") != decision_arm:
                    continue
                feature = event.payload.get("feature_snapshot")
                if not isinstance(feature, dict):
                    continue
                if feature.get("experiment_policy_version") != AI_SHADOW_V2_POLICY_VERSION:
                    continue
                key = (event.instrument_id, event.observed_at.astimezone(timezone.utc).isoformat())
                result.setdefault(key, set()).add(event.event_id)
            return result

        own_decisions = decision_keys(arm)
        pair_decisions = decision_keys(paired_arm)
        shared_keys = own_decisions.keys() & pair_decisions.keys()
        paired_decision_ids = {
            event_id
            for key in shared_keys
            for event_id in own_decisions[key]
        }
    relevant = [
        event
        for event in events
        if event.event_type == "ai_v2_decision_outcome"
        and event.payload.get("arm") == arm
    ]
    rows = [
        event.payload
        for event in relevant
        if event.payload.get("policy_version") == AI_SHADOW_V2_POLICY_VERSION
        and isinstance(event.payload.get("outcome"), dict)
        and outcome_is_valid_compat(event.payload["outcome"])
        and (
            paired_decision_ids is None
            or str(event.payload.get("decision_event_id") or "") in paired_decision_ids
        )
    ]
    avoids = [row for row in rows if row.get("alpha_state") == "avoid"]
    regretted = [row for row in avoids if row["outcome"].get("positive_opportunity") is True]
    avoid_mfe = [
        Decimal(str(row["outcome"]["mfe_pct"]))
        for row in avoids
        if row["outcome"].get("mfe_pct") is not None
    ]
    result: dict[str, Any] = {
        "decision_outcome_count": len(rows),
        "avoid_decision_count": len(avoids),
        "avoid_positive_opportunity_count": len(regretted),
        "avoid_regret_rate": str(Decimal(len(regretted)) / Decimal(len(avoids))) if avoids else None,
        "avoid_mean_mfe_pct": str(sum(avoid_mfe, Decimal("0")) / Decimal(len(avoid_mfe))) if avoid_mfe else None,
        "data_quality_excluded_decision_outcome_count": sum(
            isinstance(event.payload.get("outcome"), dict)
            and not outcome_is_valid_compat(event.payload["outcome"])
            for event in relevant
        ),
    }
    if paired_decision_ids is not None:
        result["paired_decision_count"] = len(paired_decision_ids)
        result["paired_only"] = True
    return result


def _paired_observation_stats(
    events: list[StrategyEvent],
    control_arm: str,
    catalyst_arm: str,
) -> dict[str, Any]:
    def keys(arm: str) -> set[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for event in events:
            if event.event_type != "ai_v2_decision" or event.payload.get("arm") != arm:
                continue
            feature = event.payload.get("feature_snapshot")
            if not isinstance(feature, dict) or feature.get("experiment_policy_version") != AI_SHADOW_V2_POLICY_VERSION:
                continue
            result.add((event.instrument_id, event.observed_at.astimezone(timezone.utc).isoformat()))
        return result

    control, catalyst = keys(control_arm), keys(catalyst_arm)
    return {
        "paired_observation_count": len(control & catalyst),
        "control_only_observation_count": len(control - catalyst),
        "catalyst_only_observation_count": len(catalyst - control),
        "same_observation_schedule": control == catalyst,
    }


def _delta(left: object, right: object) -> str | None:
    if left is None or right is None:
        return None
    try:
        return str(Decimal(str(left)) - Decimal(str(right)))
    except Exception:
        logger.debug("suppressed error in %s", "_delta", exc_info=True)
        return None


async def _summary_policy(
    self,
    *,
    config,
    repository,
    events,
    session_date,
    now,
):
    if not after_regular_close(now):
        return

    arm_metrics: dict[str, dict[str, Any]] = {}
    for arm in monitor._ARMS:
        metrics = hardening._episode_metrics(events, arm)
        metrics["decisions"] = _decision_outcome_metrics(events, arm)
        arm_metrics[arm] = metrics

    pair_stats = {
        "morning": _paired_observation_stats(events, "morning_control", "morning_catalyst"),
        "full_session": _paired_observation_stats(events, "full_session_control", "full_session_catalyst"),
    }
    lift = hardening._lift_metrics(events)
    decision_lift: dict[str, Any] = {}
    for name, (control_arm, catalyst_arm) in {
        "morning": ("morning_control", "morning_catalyst"),
        "full_session": ("full_session_control", "full_session_catalyst"),
    }.items():
        control = _decision_outcome_metrics(events, control_arm)
        catalyst = _decision_outcome_metrics(events, catalyst_arm)
        decision_lift[name] = {
            "control": control,
            "catalyst": catalyst,
            "catalyst_minus_control": {
                "avoid_regret_rate": _delta(catalyst.get("avoid_regret_rate"), control.get("avoid_regret_rate")),
                "avoid_mean_mfe_pct": _delta(catalyst.get("avoid_mean_mfe_pct"), control.get("avoid_mean_mfe_pct")),
            },
        }

    summary = {
        "version": monitor.AI_SHADOW_V2_VERSION,
        "policy_version": AI_SHADOW_V2_POLICY_VERSION,
        "session_date": session_date.isoformat(),
        "arms": arm_metrics,
        "comparison": {
            "catalyst_blind_control_retained": True,
            "paired_observation_schedule": pair_stats,
            "trade_count_not_primary_kpi": True,
            "alpha_execution_separated": True,
            "catalyst_changes_alpha_hurdle_only": True,
            "shared_deterministic_controls": [
                "minimum_net_r",
                "max_spread_bps",
                "halt_gate",
                "execution_eligibility",
                "entry_window",
                "force_flat",
                "kill_switch",
                "max_positions",
                "max_trades_per_day",
                "one_trade_per_symbol_per_day",
                "carried_invalidation_stop",
            ],
            "unit_shadow_sizing": True,
            "portfolio_risk_sizing_simulated": False,
            "daily_loss_budget_simulated": False,
            "open_risk_budget_simulated": False,
        },
        "research_only": True,
        "execution_authority": False,
    }
    await self._append(
        repository,
        config,
        instrument_id="__universe__",
        event_type="ai_v2_session_summary",
        state="complete",
        reason_code="AI_V2_SESSION_SUMMARY",
        observed_at=now,
        payload=summary,
        identity=(session_date.isoformat(), AI_SHADOW_V2_POLICY_VERSION, monitor._key(arm_metrics)),
    )

    await self._append(
        repository,
        config,
        instrument_id="__universe__",
        event_type="ai_v2_catalyst_lift_summary",
        state="complete",
        reason_code="AI_V2_CATALYST_CONTROL_LIFT",
        observed_at=now,
        payload={
            "version": monitor.AI_SHADOW_V2_VERSION,
            "policy_version": AI_SHADOW_V2_POLICY_VERSION,
            "session_date": session_date.isoformat(),
            "experiment": "paired_market_observations_catalyst_aware_vs_blind_control",
            "episode_metrics": lift,
            "decision_counterfactual_metrics": decision_lift,
            "pairing": pair_stats,
            "interpretation": (
                "Catalyst awareness is favored by higher recall/precision/2R rates "
                "and lower avoid regret, missed-opportunity R, MAE, or false entries. "
                "Trade count alone is not a success metric."
            ),
            "research_only": True,
            "execution_authority": False,
        },
        identity=(
            session_date.isoformat(),
            AI_SHADOW_V2_POLICY_VERSION,
            monitor._key(lift, decision_lift, pair_stats),
            "catalyst-lift",
        ),
    )
