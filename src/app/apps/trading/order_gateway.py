"""The only path to paper orders (WP-8.3).

Every placement, cancellation and replacement of a paper order goes through
``OrderGateway``; a guard test fails when any other module calls the paper
repository's order methods. The gateway decides an order's authority and the
repository enforces it in the same transaction that writes the order, under
the account lock:

- the account is enabled;
- an order that opens or adds exposure needs entry authority (a manual risk
  preview or a strategy entry authorization) and no engaged kill switch for the
  workspace, the account or the strategy (``omnix_trading_kill_switches``);
- an entry stops for the rest of the Eastern trading day once the account's
  realized loss reaches the daily limit (the strategy's risk profile, or the
  manual paper risk policy);
- accounts are long-only unless they allow shorting: a sell must be covered by
  an unreserved long position;
- the idempotency key returns the order already placed;
- a replacement cancels and places in one transaction.

A strategy entry is authorized for the order's own trade attempt: the risk
decision, universe and profile come from that attempt's events, not from the
latest decision for the instrument.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.apps.trading.us_equity_calendar import EASTERN as _ET

from .gapper_dataset import GapperCandidate
from .market_evidence import (
    DEFAULT_MARKET_EVIDENCE_POLICY,
    MARKET_EVIDENCE_POLICY_VERSION,
    classify_provider_exception,
    premarket_evidence_feature_compatible,
)
from .paper import OrderAuthority, PaperOrder, PaperOrderRequest
from .paper_risk import PaperRiskPolicy
from .strategy_evaluability import (
    assess_session_evaluability,
    build_trade_authorization,
    candidate_morning_evidence_eligible,
    finviz_membership_only_mode,
    resolve_causal_equity_bars,
    source_member_valid,
)

REDUCE_ONLY = OrderAuthority("reduce_only")
MANUAL_RISK = OrderAuthority("manual_risk", max_daily_loss_pct=PaperRiskPolicy().max_daily_loss_pct)


class OrderGateway:
    """Places, cancels and replaces paper orders with an explicit authority."""

    def __init__(self, repository: Any, *, entry_authorizer: "StrategyEntryAuthorizer | None" = None) -> None:
        self._repository = repository
        self._entry_authorizer = entry_authorizer

    def place_reducing(self, account_id: str, request: PaperOrderRequest) -> PaperOrder:
        """An order that may only reduce an existing position (exits, protections)."""
        return self._repository.place_order(account_id, request, authority=REDUCE_ONLY)

    def place_manual_entry(self, account_id: str, request: PaperOrderRequest) -> PaperOrder:
        """An entry sized by the server's risk preview for a person's request."""
        return self._repository.place_order(account_id, request, authority=MANUAL_RISK)

    def place_strategy_entry(
        self,
        account_id: str,
        request: PaperOrderRequest,
        *,
        strategy_id: str,
        trade_attempt_id: str,
        max_daily_loss_pct: Decimal | None = None,
    ) -> PaperOrder:
        """A strategy entry, authorized for its own trade attempt before it is placed."""
        if self._entry_authorizer is None:
            raise ValueError("trade_authorization_denied:AUTHORIZER_MISSING")
        self._entry_authorizer.authorize(account_id, request, trade_attempt_id=trade_attempt_id)
        authority = OrderAuthority(
            "strategy_entry",
            strategy_id=strategy_id,
            trade_attempt_id=trade_attempt_id,
            max_daily_loss_pct=max_daily_loss_pct,
        )
        return self._repository.place_order(account_id, request, authority=authority)

    def cancel(self, account_id: str, order_id: str) -> PaperOrder:
        return self._repository.cancel_order(account_id, order_id)

    def replace_manual_entry(
        self,
        account_id: str,
        order_id: str,
        replacement: PaperOrderRequest,
    ) -> tuple[PaperOrder, PaperOrder]:
        """Cancel a manual entry, place its re-sized replacement and re-point its stop, atomically (TVP-7.3)."""
        return self._repository.replace_entry(account_id, order_id, replacement, authority=MANUAL_RISK)

    def replace_reducing(
        self,
        account_id: str,
        order_id: str,
        replacement: PaperOrderRequest,
    ) -> tuple[PaperOrder, PaperOrder]:
        """Cancel and place a reducing replacement atomically."""
        return self._repository.replace_order(account_id, order_id, replacement, authority=REDUCE_ONLY)


_ORDER_METHODS = frozenset({"place_order", "cancel_order", "replace_order", "replace_entry"})


class StrategyPaperAccess:
    """A strategy's view of its paper account: reads, and orders through the gateway."""

    def __init__(
        self,
        repository: Any,
        gateway: OrderGateway,
        *,
        strategy_id: str,
        max_daily_loss_pct: Decimal | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._strategy_id = strategy_id
        self._max_daily_loss_pct = max_daily_loss_pct

    def __getattr__(self, name: str):
        if name in _ORDER_METHODS:
            raise AttributeError(f"{name} goes through the order gateway")
        return getattr(self._repository, name)

    def place_exit(self, account_id: str, request: PaperOrderRequest) -> PaperOrder:
        return self._gateway.place_reducing(account_id, request)

    def place_entry(self, account_id: str, request: PaperOrderRequest, *, trade_attempt_id: str) -> PaperOrder:
        return self._gateway.place_strategy_entry(
            account_id,
            request,
            strategy_id=self._strategy_id,
            trade_attempt_id=trade_attempt_id,
            max_daily_loss_pct=self._max_daily_loss_pct,
        )


def strategy_paper_access(repository, *, monitor, config, strategy_repository, market_service) -> StrategyPaperAccess:
    """The paper account a strategy monitor runs one configuration against."""
    from . import strategy_monitor, strategy_v2_qualification

    authorizer = StrategyEntryAuthorizer(
        monitor=monitor,
        config=config,
        strategy_repository=strategy_repository,
        market_service=market_service,
        monitor_module=strategy_monitor,
        qualification_module=strategy_v2_qualification,
    )
    gateway = OrderGateway(repository, entry_authorizer=authorizer)
    return StrategyPaperAccess(
        repository, gateway, strategy_id=config.strategy_id, max_daily_loss_pct=config.risk.max_daily_loss_pct
    )


def _morning_evidence(candidate, config):
    """Keep lightweight test doubles outside the production evidence contract."""

    if not isinstance(candidate, GapperCandidate):
        return True, ()
    return candidate_morning_evidence_eligible(candidate, config)


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _event_payload_readiness(readiness) -> dict[str, Any]:
    return readiness.model_dump(mode="json")


class StrategyEntryAuthorizer:
    """Proves a strategy entry's authority immediately before it is placed.

    The assessment (evidence, session, coverage, execution, risk sizing, entry
    window, strategy kill switch, qualification, profile and evidence policy)
    is persisted as a ``trade_authorization`` event; a denial raises
    ``ValueError("trade_authorization_denied:...")``.
    """

    def __init__(
        self,
        *,
        monitor,
        config,
        strategy_repository,
        market_service,
        monitor_module,
        qualification_module,
    ) -> None:
        self._monitor = monitor
        self._config = config
        self._strategy_repository = strategy_repository
        self._market_service = market_service
        self._monitor_module = monitor_module
        self._qualification_module = qualification_module

    def _events(self):
        return self._strategy_repository.recent_events(
            self._config.strategy_id,
            20_000,
        )

    def _persist_authorization(self, assessment, *, observed_at: datetime, payload: dict[str, Any]) -> None:
        from .strategy_repository import StrategyEvent

        idem = _key(
            assessment.strategy_id,
            assessment.trade_attempt_id,
            assessment.policy_version,
            assessment.authorized,
        )
        self._strategy_repository.append_event(
            StrategyEvent(
                strategy_id=assessment.strategy_id,
                event_id=idem[:32],
                run_id=self._monitor.current_run_id,
                instrument_id=assessment.instrument_id,
                event_type="trade_authorization",
                state="authorized" if assessment.authorized else "denied",
                reason_code=(
                    "TRADE_AUTHORIZATION_APPROVED"
                    if assessment.authorized
                    else "TRADE_AUTHORIZATION_DENIED"
                ),
                observed_at=observed_at,
                idempotency_key=idem,
                payload={
                    **payload,
                    "assessment": assessment.model_dump(mode="json"),
                    "execution_authority": assessment.authorized,
                },
            )
        )

    def authorize(self, account_id: str, request, *, trade_attempt_id: str) -> None:
        now = datetime.now(timezone.utc)
        events = self._events()
        # The order's own attempt: another attempt for the same instrument
        # (an earlier denied one, or a later one) never authorizes this order.
        risk_event = max(
            (
                event
                for event in events
                if event.event_type == "risk_decision"
                and event.instrument_id == request.instrument_id
                and isinstance(event.payload, dict)
                and event.payload.get("trade_attempt_id") == trade_attempt_id
            ),
            key=lambda event: (event.observed_at, event.event_id),
            default=None,
        )
        universe_id = (
            str(risk_event.payload.get("universe_id"))
            if risk_event is not None and risk_event.payload.get("universe_id")
            else "missing-universe"
        )
        current_profile = (
            self._qualification_module.v2_profile_fingerprint(self._config.config)
            if self._config.config.strategy_version == "2.0.0"
            else self._config.config.strategy_version
        )

        universe = None
        candidate = None
        if universe_id != "missing-universe":
            try:
                universe = self._strategy_repository.get_universe(universe_id)
            except Exception:
                universe = None
        if universe is not None:
            candidate = next(
                (
                    item
                    for item in universe.candidates
                    if item.instrument_id == request.instrument_id
                ),
                None,
            )

        source_valid = False
        morning_eligible = False
        morning_reasons: tuple[str, ...] = ("CANDIDATE_MISSING",)
        session_complete = False
        session_payload: dict[str, Any] | None = None
        coverage = None
        if universe is not None and candidate is not None:
            source_valid = source_member_valid(universe, candidate)
            morning_eligible, morning_reasons = _morning_evidence(
                candidate,
                self._config.config,
            )
            session_assessment = assess_session_evaluability(
                universe,
                self._config.config,
            )
            session_complete = session_assessment.status == "complete"
            session_payload = session_assessment.model_dump(mode="json")
            _, coverage, _ = resolve_causal_equity_bars(
                self._market_service,
                candidate,
                session_date=universe.session_date,
                observed_at=now,
                allow_shadow_fallback=False,
            )

        execution = None
        provider_readiness = None
        if candidate is not None:
            try:
                execution = self._market_service.execution_observation(
                    candidate.instrument_id,
                    candidate.binding_id,
                )
            except Exception as exc:
                provider_readiness = classify_provider_exception(
                    exc,
                    provider=DEFAULT_MARKET_EVIDENCE_POLICY.execution_provider,
                    observed_at=now,
                )
        provider_ready = (
            execution is not None
            and getattr(execution, "provider", None)
            == DEFAULT_MARKET_EVIDENCE_POLICY.execution_provider
        )
        provider_circuit_clear = provider_ready
        execution_eligible = bool(
            execution is not None
            and getattr(execution, "execution_eligible", False)
            and getattr(execution, "spread_bps", None) is not None
            and getattr(execution, "spread_bps") <= self._config.risk.max_spread_bps
        )

        risk_payload = (
            risk_event.payload.get("risk_decision")
            if risk_event is not None and isinstance(risk_event.payload, dict)
            else None
        )
        risk_valid = bool(isinstance(risk_payload, dict) and risk_payload.get("allowed") is True)
        state_events = [
            event
            for event in events
            if event.event_type == "state"
            and event.instrument_id == request.instrument_id
            and event.state == "entry_ready"
            and isinstance(event.payload, dict)
            and event.payload.get("universe_id") == universe_id
        ]
        strategy_entry_ready = bool(state_events)
        risk_profile = (
            str(risk_event.payload.get("profile_fingerprint"))
            if risk_event is not None
            and isinstance(risk_event.payload, dict)
            and risk_event.payload.get("profile_fingerprint")
            else None
        )
        profile_matches = risk_profile == current_profile
        membership_only = bool(
            candidate is not None
            and universe is not None
            and universe.discovery_source == "finviz"
            and finviz_membership_only_mode(self._config.config)
        )
        evidence_policy_matches = bool(
            membership_only
            or (
                candidate is not None
                and premarket_evidence_feature_compatible(
                    getattr(candidate, "premarket_liquidity", None)
                )
            )
        )

        now_et = now.astimezone(_ET).time()
        entry_start = getattr(
            self._config.risk,
            "entry_start_et",
            self._config.config.entry_start_et,
        )
        last_entry = getattr(
            self._config.risk,
            "last_entry_et",
            self._config.config.last_entry_et,
        )
        session_open = entry_start <= now_et <= last_entry
        kill_switch_clear = not self._config.risk.kill_switch

        qualification_authorized = True
        qualification_payload: dict[str, Any] | None = None
        if self._config.config.strategy_version == "2.0.0":
            qualification_events = self._monitor_module._v2_qualification_events(
                self._strategy_repository,
                self._config.strategy_id,
                now=now,
            )
            qualification = self._qualification_module.evaluate_v2_prospective_qualification(
                self._config,
                qualification_events,
            )
            qualification_authorized = qualification.auto_paper_authorized
            qualification_payload = qualification.model_dump(mode="json")

        assessment = build_trade_authorization(
            strategy_id=self._config.strategy_id,
            instrument_id=request.instrument_id,
            trade_attempt_id=trade_attempt_id,
            universe_id=universe_id,
            strategy_profile_fingerprint=current_profile,
            source_member_valid_value=source_valid,
            morning_evidence_eligible=morning_eligible,
            session_evaluability_complete=session_complete,
            bar_coverage_ready=bool(coverage is not None and coverage.ready),
            strategy_entry_ready=strategy_entry_ready,
            execution_observation_present=execution is not None,
            execution_eligible=execution_eligible,
            risk_sizing_valid=risk_valid,
            session_open=session_open,
            provider_ready=provider_ready,
            provider_circuit_clear=provider_circuit_clear,
            strategy_kill_switch_clear=kill_switch_clear,
            qualification_authorized=qualification_authorized,
            profile_matches=profile_matches,
            evidence_policy_matches=evidence_policy_matches,
            market_evidence_policy_version=(
                str(getattr(candidate, "market_evidence_policy_version", None))
                if candidate is not None
                and getattr(candidate, "market_evidence_policy_version", None)
                else MARKET_EVIDENCE_POLICY_VERSION
            ),
        )
        payload = {
            "order_id": request.order_id,
            "account_id": account_id,
            "morning_evidence_reason_codes": list(morning_reasons),
            "session_evaluability": session_payload,
            "bar_coverage": coverage.model_dump(mode="json") if coverage is not None else None,
            "provider_readiness": (
                _event_payload_readiness(provider_readiness)
                if provider_readiness is not None
                else {
                    "provider": getattr(execution, "provider", None),
                    "state": "READY" if provider_ready else "UNKNOWN_ERROR",
                    "ready": provider_ready,
                    "observed_at": now.isoformat(),
                }
            ),
            "fresh_execution": (
                self._monitor_module._execution_audit_payload(execution)
                if execution is not None
                else None
            ),
            "risk_decision": risk_payload,
            "qualification": qualification_payload,
        }
        self._persist_authorization(assessment, observed_at=now, payload=payload)
        if not assessment.authorized:
            raise ValueError(
                "trade_authorization_denied:"
                + ",".join(assessment.reason_codes)
            )
