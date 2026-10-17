"""Chart snapshots behind a link (TVP-2.1/2.5): ``/api/trading/snapshots``.

The chart uploads a PNG of itself and gets a link to copy (Alt+S, the header's Link button). Ids are random and
unguessable; a link opens for signed-in members of the workspace (sign-in protects every API route). Images are
checked to be PNGs and at most 3 MB; the newest ``SNAPSHOTS_KEPT`` per workspace are kept.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.persistence.unit_of_work import unit_of_work
from app.security.tenant_context import RequestTenant, TenantContext, current_tenant

MAX_SNAPSHOT_BYTES = 3 * 1024 * 1024
SNAPSHOTS_KEPT = 200
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class SnapshotCreate(BaseModel):
    # A data URL (data:image/png;base64,...) or plain base64 of a PNG.
    image: str = Field(min_length=16, max_length=4 * MAX_SNAPSHOT_BYTES // 3 + 64)
    instrument_id: str = Field(default="", max_length=200)
    interval: str = Field(default="", max_length=16)


class Snapshot(BaseModel):
    snapshot_id: str
    url: str
    instrument_id: str
    interval: str
    created_at: datetime


class SnapshotListResponse(BaseModel):
    snapshots: list[Snapshot]


def snapshot_url(snapshot_id: str) -> str:
    return f"/api/trading/snapshots/{snapshot_id}.png"


def decode_png(image: str) -> bytes:
    """The PNG bytes of an upload; ValueError for anything else."""
    if image.startswith("data:") and not image.startswith("data:image/png;base64,"):
        raise ValueError("a snapshot is a PNG")
    text = image.split(",", 1)[1] if image.startswith("data:") else image
    try:
        content = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("the image is not base64") from exc
    if not content.startswith(PNG_SIGNATURE):
        raise ValueError("a snapshot is a PNG")
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("a snapshot is at most 3 MB")
    return content


class SnapshotRepository:
    context = RequestTenant()

    def __init__(self, *, context: TenantContext | None = None, uow_factory: Callable[[], Any] = unit_of_work) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def create(self, content: bytes, *, created_by: str, instrument_id: str, interval: str) -> Snapshot:
        snapshot_id = secrets.token_urlsafe(16)
        workspace_id = self.context.workspace_id
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                INSERT INTO omnix_trading_chart_snapshots (workspace_id, snapshot_id, created_by, instrument_id, interval, image)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING created_at
                """,
                (workspace_id, snapshot_id, created_by, instrument_id, interval, content),
            ).fetchone()
            uow.connection.execute(
                """
                DELETE FROM omnix_trading_chart_snapshots
                 WHERE workspace_id = %s AND snapshot_id IN (
                       SELECT snapshot_id FROM omnix_trading_chart_snapshots WHERE workspace_id = %s
                        ORDER BY created_at DESC, snapshot_id OFFSET %s)
                """,
                (workspace_id, workspace_id, SNAPSHOTS_KEPT),
            )
            uow.commit()
        return Snapshot(snapshot_id=snapshot_id, url=snapshot_url(snapshot_id), instrument_id=instrument_id, interval=interval, created_at=row[0])

    def image(self, snapshot_id: str) -> bytes | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                "SELECT image FROM omnix_trading_chart_snapshots WHERE workspace_id = %s AND snapshot_id = %s",
                (self.context.workspace_id, snapshot_id),
            ).fetchone()
        return bytes(row[0]) if row else None

    def recent(self, limit: int = 50) -> list[Snapshot]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT snapshot_id, instrument_id, interval, created_at FROM omnix_trading_chart_snapshots
                 WHERE workspace_id = %s ORDER BY created_at DESC, snapshot_id LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
        return [
            Snapshot(snapshot_id=str(row[0]), url=snapshot_url(str(row[0])), instrument_id=str(row[1]), interval=str(row[2]), created_at=row[3])
            for row in rows
        ]

    def delete(self, snapshot_id: str) -> bool:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                "DELETE FROM omnix_trading_chart_snapshots WHERE workspace_id = %s AND snapshot_id = %s RETURNING snapshot_id",
                (self.context.workspace_id, snapshot_id),
            ).fetchone()
            uow.commit()
        return row is not None


def default_snapshot_repository() -> SnapshotRepository:
    return SnapshotRepository()


def create_trading_snapshots_router(repository_factory: Callable[[], SnapshotRepository] = default_snapshot_repository) -> APIRouter:
    router = APIRouter(prefix="/api/trading/snapshots", tags=["trading-snapshots"])

    @router.post("", response_model=Snapshot, status_code=201)
    def create(request: SnapshotCreate) -> Snapshot:
        try:
            content = decode_png(request.image)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return repository_factory().create(
            content, created_by=str(current_tenant().user_id), instrument_id=request.instrument_id, interval=request.interval,
        )

    @router.get("", response_model=SnapshotListResponse)
    def recent() -> SnapshotListResponse:
        return SnapshotListResponse(snapshots=repository_factory().recent())

    @router.get("/{snapshot_id}.png", response_class=Response, responses={200: {"content": {"image/png": {}}}})
    def image(snapshot_id: str) -> Response:
        content = repository_factory().image(snapshot_id)
        if content is None:
            raise HTTPException(status_code=404, detail="no such snapshot")
        # Snapshots never change; a browser may keep them, only for this user.
        return Response(content, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})

    @router.delete("/{snapshot_id}", status_code=204)
    def delete(snapshot_id: str) -> Response:
        if not repository_factory().delete(snapshot_id):
            raise HTTPException(status_code=404, detail="no such snapshot")
        return Response(status_code=204)

    return router


__all__ = ["create_trading_snapshots_router", "decode_png"]
