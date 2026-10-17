"""Browser error reports (WP-9.9).

The web app reports uncaught errors, unhandled rejections, render failures
and chunk-load failures to ``POST /api/client-errors``. Reports are bounded
and carry no personal data: a message, a stack trimmed by the browser, the
route path (no query string), the workspace and build. They are logged under
the request id and counted; nothing is stored.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.observability.metrics import record_client_error
from app.security.rate_limit import rate_limited

logger = logging.getLogger(__name__)

_QUERY_OR_FRAGMENT = re.compile(r"[?#][^\s)]*")


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["error", "unhandledrejection", "render", "chunk_load"]
    message: str = Field(max_length=500)
    stack: str | None = Field(default=None, max_length=4000)
    route: str = Field(max_length=200, pattern=r"^/[^?#\s]*$")
    module: str | None = Field(default=None, max_length=64)
    build: str | None = Field(default=None, max_length=64)
    # The X-Request-ID of a failed API call that caused the error, if any.
    api_request_id: str | None = Field(default=None, max_length=128)


class ClientErrorAccepted(BaseModel):
    accepted: bool = True


def _without_queries(text: str | None) -> str:
    # Script URLs in stacks may carry query strings; keep paths only.
    return _QUERY_OR_FRAGMENT.sub("", text or "")


def register_client_error_routes(router: APIRouter) -> None:
    @router.post(
        "/api/client-errors",
        response_model=ClientErrorAccepted,
        status_code=202,
        tags=["observability"],
        dependencies=[Depends(rate_limited("client_errors"))],
    )
    def report_client_error(report: ClientErrorReport) -> ClientErrorAccepted:
        record_client_error(report.kind)
        logger.warning(
            "client_error kind=%s module=%s route=%s build=%s api_request_id=%s message=%s stack=%s",
            report.kind,
            report.module or "-",
            report.route,
            report.build or "-",
            report.api_request_id or "-",
            _without_queries(report.message),
            _without_queries(report.stack)[:2000],
        )
        return ClientErrorAccepted()


__all__ = ["ClientErrorAccepted", "ClientErrorReport", "register_client_error_routes"]
