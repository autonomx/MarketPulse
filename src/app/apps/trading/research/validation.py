from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

from .contracts import ResearchValidationReport, ValidationFeatureResult, fingerprint

_FEATURES = (
    "primary_catalyst_confirmed",
    "catalyst_same_day",
    "immediate_supply_risk",
    "unresolved_supply",
    "source_authority_sufficient",
)
_NEGATIVE_FEATURES = {"immediate_supply_risk", "unresolved_supply"}
_EXACT_MARKET = {"captured", "captured_point_in_time", "exact", "paper-execution-v2"}
_EXACT_RESEARCH = {"captured_exact", "exact"}
_MIN_PROMOTION_SAMPLE = 100
_MIN_PROMOTION_EXACT_SAMPLE = 50
_MIN_PROMOTION_SYMBOLS = 3


def _r(row: dict[str, Any]) -> Decimal | None:
    value = row.get("r_result")
    return Decimal(str(value)) if value is not None else None


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _variance(values: list[Decimal]) -> float | None:
    if len(values) < 2:
        return None
    floats = [float(value) for value in values]
    mean = sum(floats) / len(floats)
    return sum((value - mean) ** 2 for value in floats) / (len(floats) - 1)


def _groups(rows: list[dict[str, Any]], key: str) -> tuple[list[Decimal], list[Decimal]]:
    yes = [_r(row) for row in rows if (row.get("features") or {}).get(key) is True]
    no = [_r(row) for row in rows if (row.get("features") or {}).get(key) is False]
    return ([value for value in yes if value is not None], [value for value in no if value is not None])


def _effect(rows: list[dict[str, Any]], key: str) -> Decimal | None:
    yes, no = _groups(rows, key)
    if not yes or not no:
        return None
    raw = (_mean(yes) or Decimal("0")) - (_mean(no) or Decimal("0"))
    return -raw if key in _NEGATIVE_FEATURES else raw


def _confidence_interval(rows: list[dict[str, Any]], key: str) -> tuple[Decimal | None, Decimal | None]:
    yes, no = _groups(rows, key)
    if len(yes) < 2 or len(no) < 2:
        return None, None
    yes_var = _variance(yes)
    no_var = _variance(no)
    effect = _effect(rows, key)
    if yes_var is None or no_var is None or effect is None:
        return None, None
    se = math.sqrt(yes_var / len(yes) + no_var / len(no))
    margin = Decimal(str(1.96 * se))
    return effect - margin, effect + margin


def _two_r_probability_delta(rows: list[dict[str, Any]], key: str) -> Decimal | None:
    yes = [
        bool(row.get("two_r_before_minus_one_r"))
        for row in rows
        if (row.get("features") or {}).get(key) is True
        and row.get("two_r_before_minus_one_r") is not None
    ]
    no = [
        bool(row.get("two_r_before_minus_one_r"))
        for row in rows
        if (row.get("features") or {}).get(key) is False
        and row.get("two_r_before_minus_one_r") is not None
    ]
    if not yes or not no:
        return None
    raw = Decimal(sum(yes)) / Decimal(len(yes)) - Decimal(sum(no)) / Decimal(len(no))
    return -raw if key in _NEGATIVE_FEATURES else raw


def _symbol_stability(rows: list[dict[str, Any]], key: str) -> tuple[int, int]:
    """Leave one symbol out and require the feature effect to remain positive.

    Per-symbol within-ticker comparisons are usually not estimable for sparse
    gappers because a symbol may appear only once. Leave-one-symbol-out instead
    tests that the measured edge is not carried by any single ticker while still
    requiring both feature states in each robustness subset.
    """
    symbols = sorted({str(row.get("instrument_id") or "") for row in rows if row.get("instrument_id")})
    usable = 0
    positive = 0
    for symbol in symbols:
        subset = [row for row in rows if str(row.get("instrument_id") or "") != symbol]
        effect = _effect(subset, key)
        if effect is None:
            continue
        usable += 1
        positive += int(effect > 0)
    return positive, usable


def _recommended_level(
    *,
    n: int,
    exact_n: int,
    ins: Decimal | None,
    outs: Decimal | None,
    exact_effect: Decimal | None,
    ci_low: Decimal | None,
    two_r_delta: Decimal | None,
    positive_symbols: int,
    usable_symbols: int,
    minimum_sample: int,
    minimum_exact_sample: int,
) -> str:
    stable = usable_symbols >= _MIN_PROMOTION_SYMBOLS and positive_symbols / usable_symbols >= 0.6
    base = (
        n >= minimum_sample
        and exact_n >= minimum_exact_sample
        and ins is not None and outs is not None
        and ins > Decimal("0.10") and outs > Decimal("0.10")
        and exact_effect is not None and exact_effect > Decimal("0")
        and ci_low is not None and ci_low > Decimal("0")
        and two_r_delta is not None and two_r_delta > Decimal("0")
        and stable
    )
    if not base or ins is None or outs is None or exact_effect is None or ci_low is None or two_r_delta is None:
        return "observe_only"
    recommendation = "score_only"
    if (
        n >= max(250, minimum_sample * 2)
        and exact_n >= max(150, minimum_exact_sample * 2)
        and ins > Decimal("0.20") and outs > Decimal("0.20")
        and exact_effect > Decimal("0.10") and ci_low > Decimal("0.05")
        and two_r_delta > Decimal("0.05")
        and usable_symbols >= _MIN_PROMOTION_SYMBOLS
        and positive_symbols / usable_symbols >= 0.65
    ):
        recommendation = "soft_gate"
    if (
        n >= max(500, minimum_sample * 4)
        and exact_n >= max(300, minimum_exact_sample * 4)
        and ins > Decimal("0.30") and outs > Decimal("0.30")
        and exact_effect > Decimal("0.20") and ci_low > Decimal("0.10")
        and two_r_delta > Decimal("0.10")
        and usable_symbols >= 5 and positive_symbols / usable_symbols >= 0.70
    ):
        recommendation = "hard_gate"
    return recommendation


def build_validation_report(
    outcomes: list[dict[str, Any]],
    *,
    policy_version: str = "trading-research-1",
    minimum_sample: int = 100,
    minimum_exact_sample: int = 50,
) -> ResearchValidationReport:
    """Produce HTR-14 evidence without granting execution authority."""
    minimum_sample = max(_MIN_PROMOTION_SAMPLE, int(minimum_sample))
    minimum_exact_sample = max(_MIN_PROMOTION_EXACT_SAMPLE, int(minimum_exact_sample))
    chronological = sorted(
        outcomes,
        key=lambda row: (str(row.get("session_date") or ""), str(row.get("outcome_id") or "")),
    )
    cut = max(1, int(len(chronological) * 0.7)) if chronological else 0
    train = chronological[:cut]
    test = chronological[cut:]
    exact = [
        row for row in outcomes
        if row.get("market_fidelity") in _EXACT_MARKET
        and row.get("research_fidelity") in _EXACT_RESEARCH
    ]
    results: list[ValidationFeatureResult] = []
    for key in _FEATURES:
        ins = _effect(train, key)
        outs = _effect(test, key)
        exact_effect = _effect(exact, key)
        ci_low, ci_high = _confidence_interval(outcomes, key)
        two_r_delta = _two_r_probability_delta(outcomes, key)
        n = sum(
            1 for row in outcomes
            if isinstance((row.get("features") or {}).get(key), bool) and _r(row) is not None
        )
        exact_n = sum(
            1 for row in exact
            if isinstance((row.get("features") or {}).get(key), bool) and _r(row) is not None
        )
        positive_symbols, usable_symbols = _symbol_stability(exact, key)
        recommendation = _recommended_level(
            n=n, exact_n=exact_n, ins=ins, outs=outs, exact_effect=exact_effect,
            ci_low=ci_low, two_r_delta=two_r_delta, positive_symbols=positive_symbols,
            usable_symbols=usable_symbols, minimum_sample=minimum_sample,
            minimum_exact_sample=minimum_exact_sample,
        )
        reasons: list[str] = []
        if n < minimum_sample: reasons.append(f"sample {n} < required {minimum_sample}")
        if exact_n < minimum_exact_sample: reasons.append(f"exact sample {exact_n} < required {minimum_exact_sample}")
        if ins is None or outs is None: reasons.append("insufficient true/false observations in chronological train/holdout")
        elif ins <= Decimal("0.10") or outs <= Decimal("0.10"): reasons.append("expectancy effect is not >0.10R in both train and holdout")
        if exact_effect is None or exact_effect <= Decimal("0"): reasons.append("effect is not positive in the exact/captured subset")
        if ci_low is None: reasons.append("95% uncertainty interval unavailable")
        elif ci_low <= Decimal("0"): reasons.append("95% uncertainty interval crosses zero")
        if two_r_delta is None: reasons.append("2R-before--1R probability delta unavailable")
        elif two_r_delta <= Decimal("0"): reasons.append("2R-before--1R probability does not improve")
        if usable_symbols < _MIN_PROMOTION_SYMBOLS:
            reasons.append(f"cross-symbol robustness unavailable: {usable_symbols} leave-one-symbol-out subsets < required {_MIN_PROMOTION_SYMBOLS}")
        elif positive_symbols / usable_symbols < 0.6:
            reasons.append(f"cross-symbol robustness weak: {positive_symbols}/{usable_symbols} leave-one-symbol-out effects positive")
        if recommendation != "observe_only":
            reasons = [
                f"recommended {recommendation} after chronological holdout and exact-subset checks",
                f"cross-symbol robustness {positive_symbols}/{usable_symbols} leave-one-symbol-out effects positive",
                "R outcomes are evaluated after the originating execution model's spread/slippage/latency assumptions rather than from raw hindsight prices",
                "recommendation remains non-authoritative until explicit review",
            ]
        results.append(ValidationFeatureResult(
            feature=key, sample_size=n, exact_sample_size=exact_n,
            in_sample_effect_r=ins, out_of_sample_effect_r=outs,
            win_probability_delta=two_r_delta,
            confidence_interval_low=ci_low, confidence_interval_high=ci_high,
            recommendation=cast(Any, recommendation),
            reason="; ".join(reasons) or "observe only",
        ))

    promotion = False
    generated = datetime.now(timezone.utc)
    payload = {
        "policy": policy_version, "sample": len(outcomes), "exact": len(exact),
        "results": [item.model_dump(mode="json") for item in results], "promotion": promotion,
    }
    fp = fingerprint(payload)
    return ResearchValidationReport(
        validation_id=f"rval-{hashlib.sha256(fp.encode()).hexdigest()[:24]}",
        policy_version=policy_version, generated_at=generated,
        sample_size=len(outcomes), exact_sample_size=len(exact),
        feature_results=tuple(results), promotion_allowed=False,
        notes=(
            "Automatic HTR-14 analysis cannot grant execution authority; explicit reviewed promotion is required.",
            "R outcomes use the backtest/paper execution models already applied by the originating run; no raw-price hindsight labels are injected.",
            "Chronological holdout, exact/captured subset, 2R probability, approximate 95% uncertainty, and leave-one-symbol-out robustness are evaluated where data exists.",
            "Recommended authority tiers are statistical candidates only; review may preserve or reduce, never strengthen, those tiers.",
            "Promotion recommendation floors are at least 100 labeled observations, 50 exact/captured labeled observations per feature, and three usable leave-one-symbol-out robustness subsets; caller inputs may raise but never lower sample floors.",
            "Per-feature sample counts exclude missing/unknown feature values and outcomes without an R label.",
        ),
        immutable_fingerprint=fp,
    )
