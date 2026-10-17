"""Hidden gateway routes for portable RPG world bundle export and import."""
from __future__ import annotations
from fastapi import APIRouter


from typing import Any

from fastapi import Body, HTTPException, Query, Request, Response

from app.apps.rpg.genesis.worlds.world_bundle import MAX_WORLD_BUNDLE_BYTES
from app.apps.rpg.genesis.worlds.world_bundle_export import export_world_bundle
from app.apps.rpg.genesis.worlds.world_bundle_import import (
    WorldBundleImportConflict,
    import_world_bundle,
)
from app.apps.rpg.edge.api.web_contracts import (
    documented,
    RpgWorldBundleImportResponse,
)

_ROUTE_SENTINEL = "_omnix_rpg_world_bundle_routes_registered"


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(
            status_code=404,
            detail={"ok": False, "error": str(exc).strip("'")},
        )
    if isinstance(exc, WorldBundleImportConflict):
        return HTTPException(
            status_code=409,
            detail={"ok": False, "error": str(exc)},
        )
    return HTTPException(
        status_code=422,
        detail={"ok": False, "error": str(exc)},
    )


def register_rpg_world_bundle_routes(router: APIRouter, state) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get(
        "/api/rpg/worlds/{world_id}/export",
        response_model=None,
        response_class=Response,
        responses={
            200: {
                "description": "Portable RPG world bundle archive.",
                "content": {
                    "application/zip": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
        },
        tags=["rpg-world"],
    )
    def rpg_export_world_bundle(world_id: str) -> Response:
        try:
            bundle = export_world_bundle(world_id)
        except (KeyError, ValueError) as exc:
            raise _error(exc) from exc
        return Response(
            content=bundle.content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{bundle.filename}"',
                "X-Omnix-World-Bundle-Version": str(bundle.manifest.version),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post(
        "/api/rpg/worlds/import",
        tags=["rpg-world"],
        responses=documented(RpgWorldBundleImportResponse),
    )
    def rpg_import_world_bundle(
        request: Request,
        bundle_content: bytes = Body(..., media_type="application/zip"),
        target_world_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_WORLD_BUNDLE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail={"ok": False, "error": "world_bundle_size_limit_exceeded"},
                    )
            except ValueError:
                pass
        content = bundle_content
        try:
            return import_world_bundle(
                content,
                target_world_id=target_world_id,
            )
        except (KeyError, ValueError) as exc:
            raise _error(exc) from exc
