"""Direct browser route for canonical local voice-clone profiles."""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from fastapi import APIRouter, Response

from app.assets.canonical_voice_clones import discover_canonical_voice_clone_assets
from app.assets.models import AssetListResponse, PublicAssetListResponse

_ROUTE_SENTINEL = "_omnix_voice_library_route_registered"
VOICE_LIBRARY_PATH = "/api/voice-library"


def register_voice_library_route(router: APIRouter, state: Any) -> None:
    """Register an uncapped endpoint backed directly by resources/voice_clones."""
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)


    @router.get(
        VOICE_LIBRARY_PATH,
        response_model=PublicAssetListResponse,
    )
    def voice_library(response: Response) -> AssetListResponse:
        assets = discover_canonical_voice_clone_assets()
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Omnix-Voice-Profile-Count"] = str(len(assets))
        response.headers["X-Omnix-Voice-Library-Source"] = "resources/voice_clones"
        return AssetListResponse(assets=assets)
