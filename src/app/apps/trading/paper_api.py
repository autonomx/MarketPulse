from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.persistence.errors import RevisionConflict

from .paper import (
    PaperAccount,
    PaperAccountCreate,
    PaperAccountSettings,
    PaperAccountSnapshot,
    PaperFill,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
)
from .order_gateway import OrderGateway
from .paper_entry_move import (
    PaperRiskEntryMoveRequest,
    PaperRiskEntryMoveResult,
    movable_entry,
    moved_entry_intent,
    snapshot_without_order,
)
from .paper_lifecycle import TradingPaperLifecycle, default_paper_lifecycle
from .paper_protection import PaperPositionProtection, PaperProtectionUpsert
from .paper_protection_repository import (
    TradingPaperProtectionRepository,
    default_paper_protection_repository,
)
from .paper_repository import TradingPaperRepository
from .paper_risk import (
    PaperRiskOrderRequest,
    PaperRiskPreview,
    PaperRiskPreviewRequest,
    paper_risk_day_bounds,
    preview_paper_risk,
    risk_order_request,
    risk_protection_request,
)
from .paper_runtime_repository import default_runtime_paper_repository
from .service import TradingMarketDataService, default_market_data_service
from .strategy_risk import paper_account_equity
from .strategy_repository import TradingStrategyRepository, default_strategy_repository


class PaperAccountListResponse(BaseModel):
    accounts: list[PaperAccount]


class PaperFillListResponse(BaseModel):
    fills: list[PaperFill]


class PaperProtectionListResponse(BaseModel):
    protections: list[PaperPositionProtection]


class PaperResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_cash: Decimal = Field(default=Decimal("100000"), ge=0)


class PaperOrderReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement: PaperOrderRequest


class PaperOrderReplaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancelled: PaperOrder
    replacement: PaperOrder


class PaperRiskOrderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview: PaperRiskPreview
    order: PaperOrder
    protection: PaperPositionProtection


RepositoryFactory = Callable[[], TradingPaperRepository]
LifecycleFactory = Callable[[], TradingPaperLifecycle]
ProtectionRepositoryFactory = Callable[[], TradingPaperProtectionRepository]
MarketServiceFactory = Callable[[], TradingMarketDataService]
StrategyRepositoryFactory = Callable[[], TradingStrategyRepository]
_ORDER_MANAGEMENT_HEADER = "X-Omnix-Paper-Order-Management"
_ORDER_MANAGEMENT_VERSION = "v2"


def _require_order_management(version: str | None) -> None:
    # Preserve the legacy disabled route for old clients while allowing the new
    # workstation to opt into explicit cancel/replace semantics.
    if version != _ORDER_MANAGEMENT_VERSION:
        raise HTTPException(status_code=409, detail="paper_order_cancellation_disabled")


def _raw_order_is_reducing_long_exposure(
    snapshot: PaperAccountSnapshot,
    request: PaperOrderRequest,
    *,
    replacing_order_id: str | None = None,
) -> bool:
    """Raw HTTP orders are exit-only; new exposure must use server risk intent.

    A raw sell is allowed only when the long position and its reservations
    prove it cannot increase or reverse exposure; a raw buy only when it buys
    back no more of a short (TVP-7.2a) than the buys already working leave.
    A replacement gives the replaced order's quantity back before the check.
    """
    def open_orders(side: str):
        return [
            order
            for order in snapshot.open_orders
            if order.instrument_id == request.instrument_id
            and order.side == side
            and order.status == "open"
            and order.order_id != replacing_order_id
        ]

    if request.side == "buy":
        short = next(
            (item for item in snapshot.positions if item.instrument_id == request.instrument_id and item.quantity < 0),
            None,
        )
        if short is None:
            return False
        working = sum((order.quantity - order.filled_quantity for order in open_orders("buy")), Decimal("0"))
        return request.quantity <= -short.quantity - working
    position = next(
        (
            item
            for item in snapshot.positions
            if item.instrument_id == request.instrument_id and item.quantity > 0
        ),
        None,
    )
    if position is None:
        return False
    reserved = position.reserved_quantity
    if replacing_order_id:
        replaced = next(
            (
                order
                for order in snapshot.open_orders
                if order.order_id == replacing_order_id
                and order.instrument_id == request.instrument_id
                and order.side == "sell"
                and order.status == "open"
            ),
            None,
        )
        if replaced is not None:
            reserved = max(
                Decimal("0"),
                reserved - max(Decimal("0"), replaced.quantity - replaced.filled_quantity),
            )
    available = max(Decimal("0"), position.quantity - reserved)
    return request.quantity <= available


def create_trading_paper_router(
    repository_factory: RepositoryFactory = default_runtime_paper_repository,
    lifecycle_factory: LifecycleFactory = default_paper_lifecycle,
    protection_repository_factory: ProtectionRepositoryFactory = default_paper_protection_repository,
    market_service_factory: MarketServiceFactory = default_market_data_service,
    strategy_repository_factory: StrategyRepositoryFactory = default_strategy_repository,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/paper", tags=["trading-paper"])

    async def risk_context(account_id: str, request: PaperRiskPreviewRequest):
        repository = repository_factory()
        protections = protection_repository_factory()
        start_time, end_time = paper_risk_day_bounds()
        try:
            snapshot, active_protections, execution, daily_realized = await asyncio.gather(
                asyncio.to_thread(repository.snapshot, account_id),
                asyncio.to_thread(protections.list, account_id, active_only=True),
                asyncio.to_thread(
                    market_service_factory().execution_observation,
                    request.instrument_id,
                    request.binding_id,
                ),
                asyncio.to_thread(
                    strategy_repository_factory().daily_paper_pnl,
                    account_id,
                    start_time=start_time,
                    end_time=end_time,
                ),
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "account_not_found" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc
        return snapshot, active_protections, execution, daily_realized

    async def evaluate_risk(account_id: str, request: PaperRiskPreviewRequest) -> PaperRiskPreview:
        snapshot, active_protections, execution, daily_realized = await risk_context(account_id, request)
        return preview_paper_risk(
            snapshot=snapshot,
            protections=active_protections,
            observation=execution,
            request=request,
            daily_realized_pnl=daily_realized,
        )

    @router.get("/accounts", response_model=PaperAccountListResponse)
    async def list_accounts(limit: int = Query(default=100, ge=1, le=500)):
        return PaperAccountListResponse(
            accounts=await asyncio.to_thread(repository_factory().list_accounts, limit)
        )

    @router.post("/accounts", response_model=PaperAccountSnapshot, status_code=201)
    async def create_account(request: PaperAccountCreate):
        try:
            return await asyncio.to_thread(repository_factory().create_account, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch("/accounts/{account_id}", response_model=PaperAccountSnapshot)
    async def update_account_settings(
        account_id: str,
        settings: PaperAccountSettings,
        if_match: int = Header(alias="If-Match", ge=1),
    ):
        """Change the account's settings at the revision the person saw: shorting (TVP-7.2a), margin by asset class and
        commission (TVP-7.2b). Fields left out keep their value; lowering margin can bring a margin call on the next pass."""
        try:
            return await asyncio.to_thread(
                repository_factory().update_account_settings, account_id, settings, expected_revision=if_match
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}", response_model=PaperAccountSnapshot)
    async def account_snapshot(account_id: str):
        try:
            return await asyncio.to_thread(repository_factory().snapshot, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/accounts/{account_id}/risk-preview",
        response_model=PaperRiskPreview,
    )
    async def risk_preview(account_id: str, request: PaperRiskPreviewRequest):
        """Return the canonical server sizing/risk decision for a proposed long entry."""
        return await evaluate_risk(account_id, request)

    @router.post(
        "/accounts/{account_id}/risk-orders",
        response_model=PaperRiskOrderResult,
        status_code=201,
    )
    async def place_risk_order(account_id: str, request: PaperRiskOrderRequest):
        """Size and submit a new entry (long, or short where the account allows it) from server-owned risk rules."""
        probe_price = request.trigger_price or Decimal("1")
        probe = PaperRiskPreviewRequest(
            instrument_id=request.instrument_id,
            binding_id=request.binding_id,
            entry_price=probe_price,
            stop_price=request.stop_loss,
            desired_risk_pct=request.desired_risk_pct,
            side=request.side,
        )
        snapshot, active_protections, execution, daily_realized = await risk_context(account_id, probe)
        # A market buy pays the ask; a market short sells at the bid.
        market_price = execution.bid if request.side == "sell" else execution.ask
        entry_price = (
            (market_price or execution.last)
            if request.order_type == "market"
            else request.worst_entry_price
        )
        if entry_price is None:
            raise HTTPException(status_code=422, detail="paper_risk_entry_price_unavailable")
        preview_request = PaperRiskPreviewRequest(
            instrument_id=request.instrument_id,
            binding_id=request.binding_id,
            entry_price=entry_price,
            stop_price=request.stop_loss,
            desired_risk_pct=request.desired_risk_pct,
            side=request.side,
        )
        preview = preview_paper_risk(
            snapshot=snapshot,
            protections=active_protections,
            observation=execution,
            request=preview_request,
            daily_realized_pnl=daily_realized,
        )
        if not preview.allowed or preview.recommended_quantity <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "paper_risk_rejected",
                    "reason_codes": list(preview.reason_codes),
                    "preview": preview.model_dump(mode="json"),
                },
            )

        repository = repository_factory()
        protection_repository = protection_repository_factory()
        try:
            protection_request = risk_protection_request(request, entry_price=entry_price)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            protection = await asyncio.to_thread(
                protection_repository.arm_pending_entry,
                account_id,
                protection_request,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "not_found" in detail else 409 if "already_submitted" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

        order_request = risk_order_request(
            request,
            entry_price=entry_price,
            quantity=preview.recommended_quantity,
        )
        try:
            order = await asyncio.to_thread(OrderGateway(repository).place_manual_entry, account_id, order_request)
        except ValueError as exc:
            cleanup_error = None
            try:
                await asyncio.to_thread(
                    protection_repository.transition,
                    account_id,
                    request.instrument_id,
                    status="cancelled",
                    exit_order_id=None,
                    trigger_reason="entry_submit_failed",
                )
            except ValueError as cleanup_exc:
                cleanup_error = str(cleanup_exc)
            detail = str(exc)
            if cleanup_error:
                detail = f"{detail}:protection_cleanup_failed:{cleanup_error}"
            status = 404 if "not_found" in detail else 409 if "insufficient" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

        return PaperRiskOrderResult(preview=preview, order=order, protection=protection)

    @router.post(
        "/accounts/{account_id}/risk-orders/{order_id}/move",
        response_model=PaperRiskEntryMoveResult,
    )
    async def move_risk_entry(
        account_id: str,
        order_id: str,
        request: PaperRiskEntryMoveRequest,
        order_management: str | None = Header(default=None, alias=_ORDER_MANAGEMENT_HEADER),
    ):
        """Re-price a working risk entry (dragged on the chart): re-sized by the server, replaced atomically."""
        _require_order_management(order_management)
        try:
            current = await asyncio.to_thread(repository_factory().snapshot, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        located = next((item for item in current.open_orders if item.order_id == order_id), None)
        if located is None:
            raise HTTPException(status_code=409, detail="paper_order_not_open")
        probe = PaperRiskPreviewRequest(
            instrument_id=located.instrument_id,
            binding_id=located.binding_id,
            entry_price=request.limit_price or request.trigger_price,
            stop_price=Decimal("0.0001"),
        )
        snapshot, active_protections, execution, daily_realized = await risk_context(account_id, probe)
        try:
            order, protection = movable_entry(snapshot, active_protections, order_id)
            intent = moved_entry_intent(order, protection, request, equity=paper_account_equity(snapshot))
            if any(item.order_id == request.order_id for item in (*snapshot.open_orders, *snapshot.order_history)):
                raise ValueError("paper_order_id_not_new")
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=409 if "not_" in detail or "filled" in detail else 422, detail=detail) from exc
        entry_price = intent.worst_entry_price
        if entry_price is None:
            raise HTTPException(status_code=422, detail="paper_risk_entry_price_unavailable")
        # Sized as if the working entry were already cancelled: its cash and its pending stop don't count twice.
        preview = preview_paper_risk(
            snapshot=snapshot_without_order(snapshot, order),
            protections=[item for item in active_protections if item is not protection],
            observation=execution,
            request=PaperRiskPreviewRequest(
                instrument_id=order.instrument_id,
                binding_id=order.binding_id,
                entry_price=entry_price,
                stop_price=intent.stop_loss,
                desired_risk_pct=intent.desired_risk_pct,
                side=intent.side,
            ),
            daily_realized_pnl=daily_realized,
        )
        if not preview.allowed or preview.recommended_quantity <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "paper_risk_rejected",
                    "reason_codes": list(preview.reason_codes),
                    "preview": preview.model_dump(mode="json"),
                },
            )
        try:
            cancelled, moved = await asyncio.to_thread(
                OrderGateway(repository_factory()).replace_manual_entry,
                account_id,
                order_id,
                risk_order_request(intent, entry_price=entry_price, quantity=preview.recommended_quantity),
            )
        except ValueError as exc:
            # The transaction rolled back: the working entry and its stop are as they were.
            detail = str(exc)
            conflict = "not_open" in detail or "insufficient" in detail or "not_movable" in detail
            raise HTTPException(status_code=409 if conflict else 422, detail=detail) from exc
        # The protection as committed (the user may have edited its levels since the snapshot).
        try:
            moved_protection = await asyncio.to_thread(protection_repository_factory().get, account_id, order.instrument_id)
        except ValueError:
            moved_protection = None
        if moved_protection is None:
            moved_protection = protection.model_copy(update={"entry_order_id": moved.order_id, "revision": protection.revision + 1})
        return PaperRiskEntryMoveResult(preview=preview, cancelled=cancelled, order=moved, protection=moved_protection)

    @router.get(
        "/accounts/{account_id}/protections",
        response_model=PaperProtectionListResponse,
    )
    async def list_protections(
        account_id: str,
        active_only: bool = Query(default=True),
    ):
        try:
            values = await asyncio.to_thread(
                protection_repository_factory().list,
                account_id,
                active_only=active_only,
            )
            return PaperProtectionListResponse(protections=values)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/accounts/{account_id}/protections/{instrument_id:path}",
        response_model=PaperPositionProtection,
    )
    async def get_protection(account_id: str, instrument_id: str):
        try:
            return await asyncio.to_thread(
                protection_repository_factory().get,
                account_id,
                instrument_id,
                include_inactive=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/accounts/{account_id}/protections",
        response_model=PaperPositionProtection,
    )
    async def upsert_protection(account_id: str, request: PaperProtectionUpsert):
        try:
            return await asyncio.to_thread(
                protection_repository_factory().upsert,
                account_id,
                request,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "not_found" in detail else 409 if "already_submitted" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

    @router.delete(
        "/accounts/{account_id}/protections/{instrument_id:path}",
        response_model=PaperPositionProtection,
    )
    async def clear_protection(account_id: str, instrument_id: str):
        try:
            return await asyncio.to_thread(
                protection_repository_factory().clear,
                account_id,
                instrument_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/accounts/{account_id}/orders",
        response_model=PaperOrder,
        status_code=201,
    )
    async def place_order(account_id: str, request: PaperOrderRequest):
        """Accept an order without manufacturing a fill from caller price data.

        reference_price is reservation-only. A market order remains open until
        the server-side monitor receives an execution-eligible market observation.
        """
        repository = repository_factory()
        try:
            snapshot = await asyncio.to_thread(repository.snapshot, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not _raw_order_is_reducing_long_exposure(snapshot, request):
            raise HTTPException(
                status_code=409,
                detail="paper_entry_requires_server_risk_authority",
            )
        try:
            return await asyncio.to_thread(OrderGateway(repository).place_reducing, account_id, request)
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "not_found" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

    @router.delete(
        "/accounts/{account_id}/orders/{order_id}",
        response_model=PaperOrder,
    )
    async def cancel_order(
        account_id: str,
        order_id: str,
        order_management: str | None = Header(default=None, alias=_ORDER_MANAGEMENT_HEADER),
    ):
        _require_order_management(order_management)
        try:
            return await asyncio.to_thread(
                OrderGateway(repository_factory()).cancel,
                account_id,
                order_id,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "account_not_found" in detail else 409 if "not_open" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

    @router.post(
        "/accounts/{account_id}/orders/{order_id}/replace",
        response_model=PaperOrderReplaceResponse,
    )
    async def replace_order(
        account_id: str,
        order_id: str,
        request: PaperOrderReplaceRequest,
        order_management: str | None = Header(default=None, alias=_ORDER_MANAGEMENT_HEADER),
    ):
        _require_order_management(order_management)
        repository = repository_factory()
        try:
            snapshot = await asyncio.to_thread(repository.snapshot, account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not _raw_order_is_reducing_long_exposure(
            snapshot,
            request.replacement,
            replacing_order_id=order_id,
        ):
            raise HTTPException(
                status_code=409,
                detail="paper_order_replacement_requires_server_risk_authority",
            )
        # Cancel and place in one transaction: a rejected replacement leaves
        # the original order open (WP-8.3).
        try:
            cancelled, replacement = await asyncio.to_thread(
                OrderGateway(repository).replace_reducing,
                account_id,
                order_id,
                request.replacement,
            )
        except ValueError as exc:
            detail = str(exc)
            conflict = "not_open" in detail or "insufficient" in detail
            status = 404 if "account_not_found" in detail else 409 if conflict else 422
            raise HTTPException(status_code=status, detail=detail) from exc
        return PaperOrderReplaceResponse(cancelled=cancelled, replacement=replacement)

    @router.post(
        "/accounts/{account_id}/observations",
        response_model=PaperFillListResponse,
    )
    def process_observation(
        account_id: str,
        observation: PaperMarketObservation,
    ):
        """Legacy compatibility endpoint; browser-supplied observations never fill."""
        del account_id, observation
        return PaperFillListResponse(fills=[])

    @router.post(
        "/accounts/{account_id}/reset",
        response_model=PaperAccountSnapshot,
    )
    async def reset_account(
        account_id: str,
        request: PaperResetRequest,
        if_match: int = Header(alias="If-Match", ge=1),
    ):
        try:
            return await asyncio.to_thread(
                lifecycle_factory().reset_account,
                account_id,
                initial_cash=request.initial_cash,
                expected_revision=if_match,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "not_found" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

    @router.delete(
        "/accounts/{account_id}",
        response_model=PaperAccountSnapshot,
    )
    async def archive_account(
        account_id: str,
        if_match: int = Header(alias="If-Match", ge=1),
    ):
        try:
            return await asyncio.to_thread(
                lifecycle_factory().archive_account,
                account_id,
                expected_revision=if_match,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router