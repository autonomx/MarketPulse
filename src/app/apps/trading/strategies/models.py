from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


StrategyMode = Literal["off", "shadow", "auto_paper"]
StrategyExecutionOwner = Literal["monitor", "runner_shadow", "runner"]
GapPullbackState = Literal[
    "discovered",
    "qualified_gap",
    "opening_impulse",
    "first_pullback",
    "first_low_confirmed",
    "bounce_high_confirmed",
    "second_pullback",
    "higher_low_confirmed",
    "vwap_reclaim",
    "lower_high_break",
    "breakout_hold",
    "entry_ready",
    "rejected",
    "expired",
]
FloatPreferenceMode = Literal["ignore", "score", "require"]
StrategyBarInterval = Literal["1m", "5m"]
GapperDiscoverySource = Literal["yahoo", "finviz"]


class StrategyRiskProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    risk_per_trade_pct: Decimal = Field(default=Decimal("0.35"), gt=0, le=5)
    max_daily_loss_pct: Decimal = Field(default=Decimal("1.5"), gt=0, le=20)
    max_open_risk_pct: Decimal = Field(default=Decimal("1.0"), gt=0, le=20)
    max_positions: int = Field(default=3, ge=1, le=50)
    max_trades_per_day: int = Field(default=5, ge=1, le=100)
    max_trade_value: Decimal = Field(default=Decimal("25000"), gt=0)
    one_trade_per_symbol_per_day: bool = True
    max_spread_bps: Decimal = Field(default=Decimal("150"), gt=0, le=10_000)
    entry_start_et: time = time(9, 35)
    last_entry_et: time = time(11, 30)
    force_flat_et: time = time(15, 55)
    kill_switch: bool = False


class GapPullbackConfig(BaseModel):
    """Versioned deterministic definition for gap_pullback_v1.

    Version 1.0 defaults remain permissive for persisted compatibility. The
    Trading UI creates 1.1.0 strategy instances with the stricter failed-selloff
    research/quality defaults explicitly populated and fully configurable.

    Version 1.2.0 reserves the same market-structure semantics for a separately
    validated HTR research-policy gate. It is fail-closed until a reviewed HTR-14
    validation artifact explicitly permits promotion; 1.0/1.1 semantics do not
    change when HTR code evolves.

    Version 2.0.0 is a separately versioned gap-as-impulse failed-selloff
    definition. It waits for a causal L1 -> B1 -> higher-L2 sequence and a
    direct B1/VWAP break. The v2-only fields below are ignored by all 1.x
    evaluators so persisted 1.x behavior remains unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: Literal["gap_pullback_v1"] = "gap_pullback_v1"
    strategy_version: Literal["1.0.0", "1.1.0", "1.2.0", "2.0.0"] = "1.0.0"

    structure_interval: StrategyBarInterval = "1m"
    execution_interval: StrategyBarInterval = "1m"

    # Research/discovery happens before entry authorization. The archive is
    # evidence-only and never changes the active live-trading universe.
    universe_scan_time_et: time = time(9, 20)
    universe_discovery_source: GapperDiscoverySource = "yahoo"
    auto_archive_daily_universe: bool = True
    universe_archive_grace_minutes: int = Field(default=10, ge=1, le=60)
    universe_discovery_count: int = Field(default=50, ge=1, le=100)
    minimum_gap_pct: Decimal = Field(default=Decimal("20"), ge=0)
    minimum_price: Decimal = Field(default=Decimal("0.50"), gt=0)
    maximum_price: Decimal = Field(default=Decimal("20"), gt=0)
    minimum_premarket_dollar_volume: Decimal = Field(default=Decimal("1000000"), ge=0)
    minimum_tod_rvol: Decimal = Field(default=Decimal("2"), ge=0)
    allow_missing_tod_rvol: bool = False
    maximum_spread_bps: Decimal = Field(default=Decimal("150"), gt=0)
    preferred_float_min_shares: Decimal = Field(default=Decimal("2000000"), gt=0)
    preferred_float_max_shares: Decimal = Field(default=Decimal("30000000"), gt=0)
    float_preference_mode: FloatPreferenceMode = "ignore"

    require_catalyst_evidence: bool = False
    reject_dilution_flags: tuple[str, ...] = ()

    opening_impulse_min_pct: Decimal = Field(default=Decimal("8"), ge=0)
    pullback_min_pct: Decimal = Field(default=Decimal("3"), ge=0)
    pullback_max_pct: Decimal = Field(default=Decimal("35"), gt=0)
    pullback_volume_max_ratio: Decimal = Field(default=Decimal("5"), ge=0, le=5)
    higher_low_buffer_bps: Decimal = Field(default=Decimal("20"), ge=0)
    breakout_volume_ratio: Decimal = Field(default=Decimal("1.25"), gt=0)
    pivot_left_bars: int = Field(default=2, ge=1, le=10)
    pivot_right_bars: int = Field(default=2, ge=1, le=10)
    volume_lookback_bars: int = Field(default=10, ge=2, le=100)

    require_breakout_hold: bool = False
    breakout_hold_bars: int = Field(default=1, ge=1, le=5)
    breakout_hold_tolerance_bps: Decimal = Field(default=Decimal("25"), ge=0, le=1000)
    minimum_quality_score: int = Field(default=0, ge=0, le=10)
    # 1.2.0 only: let reviewed research change the setup score. Off, research
    # may still block an entry through its hard gates but never raises or
    # lowers the score. Changing it is a strategy update: it needs
    # trading:strategies:admin and is audited (WP-8.3).
    research_score_adjustment_enabled: bool = False

    # 2.0.0-only causal failed-selloff geometry/management. These defaults are
    # the V11 prospective profile selected before the external April/May check.
    v2_recovery_min_pct: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    v2_second_pullback_min_pct: Decimal = Field(default=Decimal("2"), ge=0, le=100)
    v2_minimum_l1_to_b1_minutes: int = Field(default=4, ge=0, le=120)
    v2_maximum_l2_to_signal_minutes: int = Field(default=8, ge=1, le=390)
    v2_minimum_breakout_volume_ratio: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    v2_profit_protection_trigger_r: Decimal | None = Field(default=Decimal("0.75"), gt=0, le=20)
    v2_protected_stop_r: Decimal = Field(default=Decimal("0.25"), ge=0, le=20)
    v2_max_hold_minutes: int = Field(default=60, ge=1, le=390)

    stop_buffer_bps: Decimal = Field(default=Decimal("15"), ge=0)
    reward_multiple: Decimal = Field(default=Decimal("2"), gt=0, le=10)
    exit_rsi_period: int = Field(default=14, ge=2, le=100)
    exit_rsi_threshold: Decimal = Field(default=Decimal("50"), ge=0, le=100)
    entry_start_et: time = time(9, 35)
    last_entry_et: time = time(11, 30)

    # Research-only learning loop. Existing persisted strategies remain opt-in.
    # This flag never changes deterministic entry/exit authorization.
    intraday_learning_enabled: bool = False
    # Research-only 3m Stoch-RSI trend-capture overlay. It never changes
    # deterministic entry/exit authority or AUTO PAPER qualification.
    stoch_trend_capture_enabled: bool = False
    intraday_llm_enabled: bool = False
    intraday_llm_top_n: int = Field(default=5, ge=1, le=20)
    # Heartbeat for top active names; material events can invoke the LLM sooner.
    intraday_llm_interval_minutes: int = Field(default=10, ge=5, le=60)

    # Which scheduled task runs this configuration (strategy runner WP). The
    # monitor by default; ``runner_shadow`` keeps the monitor as owner while the
    # strategy runner records its own proposals as parity evidence; ``runner``
    # moves the whole pass to the strategy runner. Both run the same pass and
    # entry path through the same order gateway, so the owner never changes
    # what may be traded.
    execution_owner: StrategyExecutionOwner = "monitor"

    @model_validator(mode="after")
    def validate_range(self):
        if self.maximum_price <= self.minimum_price:
            raise ValueError("maximum_price must exceed minimum_price")
        if self.pullback_max_pct <= self.pullback_min_pct:
            raise ValueError("pullback_max_pct must exceed pullback_min_pct")
        if self.preferred_float_max_shares <= self.preferred_float_min_shares:
            raise ValueError("preferred_float_max_shares must exceed preferred_float_min_shares")
        interval_minutes = {"1m": 1, "5m": 5}
        if interval_minutes[self.execution_interval] > interval_minutes[self.structure_interval]:
            raise ValueError("execution_interval cannot be coarser than structure_interval")
        if interval_minutes[self.structure_interval] % interval_minutes[self.execution_interval] != 0:
            raise ValueError("structure_interval must be an integer multiple of execution_interval")
        if self.strategy_version == "2.0.0" and (
            self.structure_interval != "1m" or self.execution_interval != "1m"
        ):
            raise ValueError("gap_pullback_v1 2.0.0 requires 1m structure and execution intervals")
        if (
            self.strategy_version == "2.0.0"
            and self.v2_profit_protection_trigger_r is not None
            and self.v2_protected_stop_r >= self.v2_profit_protection_trigger_r
        ):
            raise ValueError("v2 protected stop R must be below the profit-protection trigger R")
        if self.stoch_trend_capture_enabled and not self.intraday_learning_enabled:
            raise ValueError("stoch trend capture requires intraday learning")
        if self.intraday_llm_enabled and not self.intraday_learning_enabled:
            raise ValueError("intraday LLM analysis requires intraday learning")
        return self


class StochRsi5mConfig(BaseModel):
    """Deterministic five-minute Stoch RSI strategy configuration.

    This strategy is intentionally shadow-only. It records causal signal
    evidence for research/replay and does not expose an AUTO PAPER execution
    path.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: Literal["stoch_rsi_5m_v1"] = "stoch_rsi_5m_v1"
    strategy_version: Literal["1.0.0"] = "1.0.0"

    # Daily universe archival fields are shared with the strategy monitor.
    universe_scan_time_et: time = time(9, 20)
    universe_discovery_source: GapperDiscoverySource = "yahoo"
    auto_archive_daily_universe: bool = True
    universe_archive_grace_minutes: int = Field(default=10, ge=1, le=60)
    universe_discovery_count: int = Field(default=50, ge=1, le=100)
    minimum_gap_pct: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_price: Decimal = Field(default=Decimal("0.50"), gt=0)
    maximum_price: Decimal = Field(default=Decimal("20"), gt=0)
    minimum_premarket_dollar_volume: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_tod_rvol: Decimal = Field(default=Decimal("0"), ge=0)
    allow_missing_tod_rvol: bool = True
    maximum_spread_bps: Decimal = Field(default=Decimal("150"), gt=0)
    preferred_float_min_shares: Decimal = Field(default=Decimal("1"), gt=0)
    preferred_float_max_shares: Decimal = Field(default=Decimal("1000000000"), gt=0)
    float_preference_mode: FloatPreferenceMode = "ignore"
    require_catalyst_evidence: bool = False
    reject_dilution_flags: tuple[str, ...] = ()

    # Trading rule: a %K observation below the configured oversold threshold
    # arms a setup. A later %K cross above %D arms momentum confirmation, and
    # %K must then cross the configured recovery threshold while still rising
    # and above %D before price confirmation can authorize an entry.
    # The actual entry open must also be strictly above the 5-period 5m EMA.
    # Entries are allowed through 15:50 ET; all research positions are
    # considered flat at 15:55 ET. Open positions exit after a finalized 5m
    # close below the 5-period 5m EMA, or a finalized %K/%D cross down while
    # %K is below 80. After an exit, a fresh setup may produce another
    # sequential trade during the same session.
    #
    # ``early_single`` keeps only the first current-session trade per symbol
    # and vetoes it when the pre-entry regular-session range exceeds 150%.
    trade_selection: Literal["sequential", "early_single"] = "sequential"
    oversold_threshold: Decimal = Field(default=Decimal("18"), gt=0, lt=100)
    recovery_threshold: Decimal = Field(default=Decimal("20"), gt=0, lt=100)
    overbought_threshold: Decimal = Field(default=Decimal("95"), gt=0, le=100)
    rsi_period: int = Field(default=14, ge=2, le=100)
    stochastic_period: int = Field(default=14, ge=2, le=100)
    k_smoothing_period: int = Field(default=3, ge=1, le=20)
    d_smoothing_period: int = Field(default=3, ge=1, le=20)
    entry_start_et: time = time(9, 35)
    last_entry_et: time = time(15, 50)
    force_flat_et: time = time(15, 55)

    @model_validator(mode="before")
    @classmethod
    def discard_removed_lower_low_fields(cls, value):
        """Accept and discard fields from configs persisted before v6."""

        if isinstance(value, dict):
            cleaned = dict(value)
            cleaned.pop("minimum_swing_bounce_pct", None)
            cleaned.pop("lower_low_close_break_pct", None)
            return cleaned
        return value

    @model_validator(mode="after")
    def validate_extremes_and_schedule(self):
        if self.recovery_threshold <= self.oversold_threshold:
            raise ValueError("recovery_threshold must exceed oversold_threshold")
        if self.recovery_threshold >= self.overbought_threshold:
            raise ValueError("recovery_threshold must be below overbought_threshold")
        if self.overbought_threshold <= self.oversold_threshold:
            raise ValueError("overbought_threshold must exceed oversold_threshold")
        if self.last_entry_et < self.entry_start_et:
            raise ValueError("last_entry_et must be at or after entry_start_et")
        if self.force_flat_et < self.last_entry_et:
            raise ValueError("force_flat_et must be at or after last_entry_et")
        if self.maximum_price <= self.minimum_price:
            raise ValueError("maximum_price must exceed minimum_price")
        if self.preferred_float_max_shares <= self.preferred_float_min_shares:
            raise ValueError("preferred_float_max_shares must exceed preferred_float_min_shares")
        return self


OvernightArmId = Literal["day2-runner", "strong-close"]


class OvernightArmConfig(BaseModel):
    """One overnight research arm: a candidate qualifies when every rule holds.

    ``change`` is the decision price versus the prior regular close; ``volume_ratio``
    is the session's regular volume so far versus the 20-session average daily
    volume; ``close_location`` is where the price sits in the session's range
    (0 = low, 1 = high).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: OvernightArmId
    enabled: bool = True
    min_change: Decimal = Field(ge=0, le=10)
    max_change: Decimal | None = Field(default=None, gt=0, le=100)
    min_volume_ratio: Decimal = Field(ge=0, le=1_000)
    min_close_location: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_change_band(self):
        if self.max_change is not None and self.max_change <= self.min_change:
            raise ValueError("max_change must exceed min_change")
        return self


def _default_overnight_arms() -> tuple[OvernightArmConfig, ...]:
    return (
        OvernightArmConfig(arm_id="day2-runner", min_change=Decimal("0.30"),
                           min_volume_ratio=Decimal("5"), min_close_location=Decimal("0.80")),
        OvernightArmConfig(arm_id="strong-close", min_change=Decimal("0.10"), max_change=Decimal("0.30"),
                           min_volume_ratio=Decimal("3"), min_close_location=Decimal("0.90")),
    )


class OvernightShadowConfig(BaseModel):
    """Overnight-gap research group: pick near the close, label the next morning.

    SHADOW only. At ``decision_et`` each enabled arm picks up to
    ``max_picks_per_arm`` common stocks from a full-market scan of regular-session
    5-minute SIP bars that ended by ``data_cutoff_et`` (the free SIP feed is
    15 minutes delayed). Picks are labelled after ``label_after_et`` on the next
    session: entry at the ``entry_et`` one-minute open, primary exit at the 09:30
    open, plus the last premarket trade before 08:00 and the 09:45 open.
    Nothing here places, sizes or routes an order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: Literal["overnight_shadow_v1"] = "overnight_shadow_v1"
    strategy_version: Literal["1.0.0"] = "1.0.0"
    data_cutoff_et: time = time(15, 30)
    decision_et: time = time(15, 50)
    entry_et: time = time(15, 55)
    label_after_et: time = time(10, 5)
    minimum_price: Decimal = Field(default=Decimal("1"), gt=0)
    maximum_price: Decimal = Field(default=Decimal("20"), gt=0)
    max_picks_per_arm: int = Field(default=5, ge=1, le=20)
    adv_sessions: int = Field(default=20, ge=5, le=60)
    min_adv_sessions: int = Field(default=10, ge=1, le=60)
    arms: tuple[OvernightArmConfig, ...] = Field(default_factory=_default_overnight_arms, min_length=1)

    @model_validator(mode="after")
    def validate_overnight_config(self):
        if self.maximum_price <= self.minimum_price:
            raise ValueError("maximum_price must exceed minimum_price")
        if not (self.data_cutoff_et < self.decision_et < self.entry_et):
            raise ValueError("data_cutoff_et < decision_et < entry_et is required")
        if self.min_adv_sessions > self.adv_sessions:
            raise ValueError("min_adv_sessions must not exceed adv_sessions")
        arm_ids = [arm.arm_id for arm in self.arms]
        if len(set(arm_ids)) != len(arm_ids):
            raise ValueError("overnight arm ids must be unique")
        return self


class GapPullbackFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_pct: Decimal
    opening_impulse_pct: Decimal | None = None
    pullback_depth_pct: Decimal | None = None
    impulse_average_volume: Decimal | None = None
    pullback_selling_average_volume: Decimal | None = None
    pullback_volume_ratio: Decimal | None = None
    l1: Decimal | None = None
    b1: Decimal | None = None
    l2: Decimal | None = None
    second_pullback_depth_pct: Decimal | None = None
    l1_to_b1_minutes: int | None = None
    l2_to_signal_minutes: int | None = None
    session_vwap: Decimal | None = None
    vwap_distance_pct: Decimal | None = None
    breakout_volume_ratio: Decimal | None = None
    breakout_hold_bars: int | None = None
    spread_bps: Decimal | None = None
    tod_rvol: Decimal | None = None
    float_shares: Decimal | None = None
    catalyst_evidence_count: int = 0
    dilution_flags: tuple[str, ...] = ()
    catalyst_score: int = Field(default=0, ge=0, le=2)
    supply_score: int = Field(default=0, ge=0, le=2)
    opening_structure_score: int = Field(default=0, ge=0, le=2)
    pullback_quality_score: int = Field(default=0, ge=0, le=2)
    reclaim_break_score: int = Field(default=0, ge=0, le=2)
    quality_score: int = Field(default=0, ge=0, le=10)
    minutes_since_open: int | None = None


class StrategySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    state: GapPullbackState
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    risk_per_share: Decimal
    reason_code: str
    quality_score: int = Field(default=0, ge=0, le=10)


class GapPullbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    state: GapPullbackState
    reason_code: str
    features: GapPullbackFeatures
    transitions: tuple[GapPullbackState, ...]
    signal: StrategySignal | None = None
    evaluated_bar_count: int = Field(ge=0)
