from __future__ import annotations

from app.config.env import environment

"""Idempotent startup provisioning for the managed interday SHADOW profile."""

from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from app.persistence.errors import RevisionConflict

from .paper import PaperAccountCreate
from .paper_repository import TradingPaperRepository
from .strategies.models import StrategyRiskProfile
from .strategy_repository import (
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
)
from .strategy_v2_qualification import managed_finviz_v2_config
from .trade_logging import trade_log


INTERDAY_TRADING_STRATEGY_ID = "interday-trading-strategy-shadow"
# Compatibility name for callers that still refer to the managed Finviz
# provisioner. The durable strategy identity is the interday group ID above.
MANAGED_FINVIZ_SHADOW_STRATEGY_ID = INTERDAY_TRADING_STRATEGY_ID
MANAGED_FINVIZ_SHADOW_ACCOUNT_ID = "omnix-finviz-shadow"
_MANAGED_ACCOUNT_NAME = "Omnix Finviz SHADOW"
_MAX_UPDATE_ATTEMPTS = 3

# The first five are embedded research arms of the parent configuration. The
# final three are durable child strategy configurations linked by
# TradingStrategyConfigDocument.parent_strategy_id.
INTERDAY_TRADING_SUBSTRATEGY_KEYS = (
    "deterministic-v2",
    "stoch-trend-capture",
    "leader-momentum-continuation",
    "ai-every-minute",
    "ai-event-driven",
    "stoch-rsi-5min",
    "stoch-rsi-5min-early-single",
    "gap-pullback-v2-prospective-20260825",
)


class ManagedFinvizShadowProvisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = MANAGED_FINVIZ_SHADOW_STRATEGY_ID
    account_id: str | None = None
    action: Literal[
        "created",
        "updated",
        "unchanged",
        "archived_suppressed",
        "disabled",
    ]
    enabled: bool
    mode: Literal["shadow", "auto_paper"] = "shadow"
    detail: str | None = None


def _flag(name: str, default: str) -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def managed_finviz_shadow_autoprovision_enabled() -> bool:
    """Production defaults on; legacy tests remain opt-in."""

    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_FINVIZ_SHADOW_AUTOPROVISION", "1")


def managed_finviz_shadow_config():
    """Server-canonical equivalent of the UI Finviz Learning V2 preset."""

    return managed_finviz_v2_config()


def managed_finviz_shadow_document(account_id: str) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id=MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
        account_id=account_id,
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode="shadow",
        active_universe_id=None,
        config=managed_finviz_shadow_config(),
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def _managed_initial_cash() -> Decimal:
    raw = environment().get("OMNIX_TRADING_FINVIZ_SHADOW_INITIAL_CASH", "1000")
    value = Decimal(raw)
    if value <= 0:
        raise ValueError("managed_finviz_shadow_initial_cash_must_be_positive")
    return value


def _resolve_account(paper_repository: TradingPaperRepository) -> str:
    requested = environment().get("OMNIX_TRADING_FINVIZ_SHADOW_ACCOUNT_ID", "").strip()
    managed_id = requested or MANAGED_FINVIZ_SHADOW_ACCOUNT_ID
    accounts = paper_repository.list_accounts(limit=500)
    by_id = {account.account_id: account for account in accounts}

    existing = by_id.get(managed_id)
    if existing is not None:
        # SHADOW never places orders, so a disabled paper account is still a
        # valid durable FK/evidence owner. Startup must not silently re-enable
        # an operator-disabled account.
        return existing.account_id

    if requested:
        raise ValueError(f"managed_finviz_shadow_account_not_found:{requested}")

    request = PaperAccountCreate(
        account_id=MANAGED_FINVIZ_SHADOW_ACCOUNT_ID,
        name=_MANAGED_ACCOUNT_NAME,
        initial_cash=_managed_initial_cash(),
        commission_bps=Decimal("0"),
    )
    try:
        snapshot = paper_repository.create_account(request)
    except Exception:
        # Multi-worker startup can race here. If another worker created the
        # stable managed account first, converge on that durable row.
        raced = {
            account.account_id: account
            for account in paper_repository.list_accounts(limit=500)
        }.get(MANAGED_FINVIZ_SHADOW_ACCOUNT_ID)
        if raced is None:
            raise
        return raced.account_id
    return snapshot.account.account_id


def _desired_profile_for_current(
    current: TradingStrategyConfigDocument,
    desired_shadow: TradingStrategyConfigDocument,
) -> TradingStrategyConfigDocument:
    """Preserve an already-authorized AUTO PAPER promotion across restart.

    Startup is allowed to restore the managed research profile, but it must
    never grant AUTO PAPER authority. The only promoted state preserved here is
    one that is already enabled and persisted as auto_paper; the strategy
    runtime re-checks the V2 qualification fingerprint before every execution
    cycle. An operator-disabled/off strategy falls back to SHADOW on startup.
    """

    if current.mode == "auto_paper" and current.enabled:
        return desired_shadow.model_copy(
            update={
                "mode": "auto_paper",
                # The managed profile consumes the current day's immutable
                # strategy-owned archive at runtime. Carrying yesterday's
                # explicit universe across restart would make the next session
                # fail with UNIVERSE_SESSION_MISMATCH.
                "active_universe_id": None,
            }
        )
    return desired_shadow


def _desired_for_current(
    current: TradingStrategyConfigDocument,
    desired_shadow: TradingStrategyConfigDocument,
) -> TradingStrategyConfigDocument:
    """The restored profile, keeping which task runs it.

    ``execution_owner`` is the operator's choice (strategy runner WP): startup
    restores the research profile but must not move the strategy back to the
    monitor, or end a runner shadow period, behind the operator's back.
    """

    desired = _desired_profile_for_current(current, desired_shadow)
    owner = getattr(current.config, "execution_owner", "monitor")
    return desired.model_copy(
        update={"config": desired.config.model_copy(update={"execution_owner": owner})}
    )


def _managed_fields_match(
    current: TradingStrategyConfigDocument,
    desired: TradingStrategyConfigDocument,
) -> bool:
    return (
        current.account_id == desired.account_id
        and current.strategy_kind == desired.strategy_kind
        and current.strategy_version == desired.strategy_version
        and current.mode == desired.mode
        and current.active_universe_id == desired.active_universe_id
        and current.config == desired.config
        and current.risk == desired.risk
        and current.enabled is True
    )


def provision_managed_finviz_shadow_strategy(
    *,
    strategy_repository: TradingStrategyRepository | None = None,
    paper_repository: TradingPaperRepository | None = None,
) -> ManagedFinvizShadowProvisionResult:
    """Create or restore the managed Finviz profile at application startup.

    New/disabled/off profiles start in SHADOW. If the exact managed strategy was
    already promoted to enabled AUTO PAPER through the normal qualification and
    review path, startup preserves that mode instead of silently demoting it.
    This provisioner never performs promotion itself. An explicit archive remains
    an operator-level opt-out and is never silently resurrected.
    """

    if not managed_finviz_shadow_autoprovision_enabled():
        return ManagedFinvizShadowProvisionResult(
            action="disabled",
            enabled=False,
            detail="autoprovision_disabled",
        )

    strategy_repo = strategy_repository or TradingStrategyRepository()
    paper_repo = paper_repository or TradingPaperRepository()

    try:
        preexisting = strategy_repo.get_config(
            MANAGED_FINVIZ_SHADOW_STRATEGY_ID
        )
    except ValueError as exc:
        if str(exc) != "strategy_config_not_found":
            raise
    else:
        if preexisting.archived_at is not None:
            return ManagedFinvizShadowProvisionResult(
                account_id=preexisting.account_id,
                action="archived_suppressed",
                enabled=False,
                detail="explicit_operator_archive",
            )

    account_id = _resolve_account(paper_repo)
    desired = managed_finviz_shadow_document(account_id)

    for attempt in range(_MAX_UPDATE_ATTEMPTS):
        try:
            current = strategy_repo.get_config(MANAGED_FINVIZ_SHADOW_STRATEGY_ID)
        except ValueError as exc:
            if str(exc) != "strategy_config_not_found":
                raise
            try:
                created = strategy_repo.create_config(desired)
            except Exception:
                # A second startup worker may have inserted the stable strategy
                # ID between the read and create. Re-read and converge instead
                # of treating that harmless race as a provisioning failure.
                try:
                    current = strategy_repo.get_config(
                        MANAGED_FINVIZ_SHADOW_STRATEGY_ID
                    )
                except ValueError as reread_exc:
                    if str(reread_exc) == "strategy_config_not_found":
                        raise
                    raise
            else:
                trade_log(
                    "auto_trading",
                    "managed_finviz_shadow_provisioned",
                    strategy_id=created.strategy_id,
                    account_id=created.account_id,
                    action="created",
                    mode=cast(Any, created.mode),
                    enabled=created.enabled,
                )
                return ManagedFinvizShadowProvisionResult(
                    account_id=created.account_id,
                    action="created",
                    enabled=created.enabled,
                    mode=cast(Any, created.mode),
                )

        if current.archived_at is not None:
            trade_log(
                "auto_trading",
                "managed_finviz_shadow_provision_suppressed",
                strategy_id=current.strategy_id,
                account_id=current.account_id,
                action="archived_suppressed",
                archived_at=current.archived_at,
            )
            return ManagedFinvizShadowProvisionResult(
                account_id=current.account_id,
                action="archived_suppressed",
                enabled=False,
                detail="explicit_operator_archive",
            )

        desired_for_current = _desired_for_current(current, desired)
        if _managed_fields_match(current, desired_for_current):
            return ManagedFinvizShadowProvisionResult(
                account_id=current.account_id,
                action="unchanged",
                enabled=True,
                mode=cast(Any, current.mode),
            )

        replacement = desired_for_current.model_copy(
            update={
                "revision": current.revision,
                "created_at": current.created_at,
                "updated_at": current.updated_at,
            }
        )
        try:
            updated = strategy_repo.update_config(
                MANAGED_FINVIZ_SHADOW_STRATEGY_ID,
                replacement,
                expected_revision=current.revision,
            )
        except RevisionConflict:
            if attempt + 1 >= _MAX_UPDATE_ATTEMPTS:
                raise
            continue

        trade_log(
            "auto_trading",
            "managed_finviz_shadow_provisioned",
            strategy_id=updated.strategy_id,
            account_id=updated.account_id,
            action="updated",
            mode=cast(Any, updated.mode),
            enabled=updated.enabled,
            revision=updated.revision,
        )
        return ManagedFinvizShadowProvisionResult(
            account_id=updated.account_id,
            action="updated",
            enabled=updated.enabled,
            mode=cast(Any, updated.mode),
        )

    raise RuntimeError("managed_finviz_shadow_provision_retry_exhausted")


__all__ = [
    "INTERDAY_TRADING_STRATEGY_ID",
    "INTERDAY_TRADING_SUBSTRATEGY_KEYS",
    "MANAGED_FINVIZ_SHADOW_ACCOUNT_ID",
    "MANAGED_FINVIZ_SHADOW_STRATEGY_ID",
    "ManagedFinvizShadowProvisionResult",
    "managed_finviz_shadow_autoprovision_enabled",
    "managed_finviz_shadow_config",
    "managed_finviz_shadow_document",
    "provision_managed_finviz_shadow_strategy",
]
