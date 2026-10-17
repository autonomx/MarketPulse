from __future__ import annotations

from collections.abc import Callable
from inspect import signature
from typing import Any

from fastapi import APIRouter, FastAPI


def include_router_registrar(
    app: FastAPI,
    registrar: Callable[..., None],
    *args: Any,
    **kwargs: Any,
) -> APIRouter:
    router = APIRouter()
    if "state" in signature(registrar).parameters:
        kwargs.setdefault("state", app.state)
    registrar(router, *args, **kwargs)
    app.include_router(router)
    return router


class _EffectiveRoute:
    """A route context whose ``path`` is always populated, including WebSockets."""

    def __init__(self, context: Any) -> None:
        self._context = context
        self.path = context.path or getattr(context.original_route, "path", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


def effective_routes(app_or_router: Any) -> list[Any]:
    """Return routes with included-router prefixes resolved (FastAPI >= 0.141)."""
    from fastapi.routing import iter_route_contexts

    return [_EffectiveRoute(context) for context in iter_route_contexts(app_or_router.routes)]
