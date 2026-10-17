"""Browser speech transport through the gateway; sidecar credentials stay server-side."""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Request, WebSocket
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from app.runtime.config import get_runtime_config
from app.security.model_service import max_upload_bytes
from app.security.service_token import service_headers

_MAX_RESPONSE_BYTES = 512 * 1024


class STTProxyResponse(BaseModel):
    """Known fields in the configured STT service response, with forward compatibility."""

    model_config = ConfigDict(extra="allow")

    ok: bool | None = None
    eligible: bool | None = None
    mode: str | None = None
    provider: str | None = None
    reasons: list[str] | None = None
    success: bool | None = None
    text: str | None = None
    segments: list[dict[str, object]] | None = None
    duration: float | None = None
    inference_ms: float | None = None



def _stt_base_url() -> str:
    endpoint = get_runtime_config().stt
    return endpoint.url.removesuffix("/transcribe") if endpoint else "http://127.0.0.1:5201"


def _ServiceConnect(uri: str, **kwargs):
    """Create a client that rejects redirects to a new credential audience."""
    from websockets.exceptions import SecurityError
    from websockets.legacy.client import Connect

    class ServiceConnect(Connect):
        def handle_redirect(self, target_uri: str) -> None:
            raise SecurityError("model_service_redirect_rejected")

    return ServiceConnect(uri, **kwargs)


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code, "request_id": secrets.token_urlsafe(18)},
                        status_code=status, headers={"Cache-Control": "no-store"})


def _query(request: Request | WebSocket, *, authority: bool = False) -> dict[str, str]:
    language = request.query_params.get("language", "en")
    if len(language) > 32 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in language):
        raise ValueError("invalid_language")
    query = {"language": language}
    if authority:
        mode = request.query_params.get("mode", "auto")
        if mode not in {"auto", "test", "observational"}:
            raise ValueError("invalid_mode")
        query["mode"] = mode
    return query


class _UploadLimit(Exception):
    pass


async def _proxy_http(request: Request, path: str, *, authority: bool = False) -> STTProxyResponse | JSONResponse:
    try:
        query = _query(request, authority=authority)
    except ValueError:
        return _error(422, "invalid_request")
    try:
        headers = service_headers()
    except RuntimeError:
        return _error(503, "service_credential_unavailable")
    limit = max_upload_bytes()
    consumed = 0

    async def body():
        nonlocal consumed
        async for chunk in request.stream():
            consumed += len(chunk)
            if consumed > limit:
                raise _UploadLimit()
            yield chunk

    if request.method == "POST":
        # Do not forward browser authorization, cookies, Host, or service headers.
        content_types = request.headers.getlist("content-type")
        if len(content_types) != 1 or not content_types[0].lower().startswith("multipart/form-data;"):
            return _error(422, "invalid_request")
        headers["Content-Type"] = content_types[0]
        lengths = request.headers.getlist("content-length")
        if lengths:
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                return _error(400, "invalid_request")
            if int(lengths[0]) > limit:
                return _error(413, "upload_too_large")
    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=120.0) as client:
            async with client.stream(request.method, f"{_stt_base_url()}{path}",
                                     params=query, headers=headers,
                                     content=body() if request.method == "POST" else None) as upstream:
                if consumed > limit:
                    return _error(413, "upload_too_large")
                if upstream.status_code >= 300:
                    status = upstream.status_code
                    if status == 413:
                        return _error(413, "upload_too_large")
                    if status in {400, 422}:
                        return _error(status, "invalid_request")
                    return _error(503, "stt_unavailable")
                data = bytearray()
                async for chunk in upstream.aiter_bytes():
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        return _error(502, "invalid_stt_response")
                    data.extend(chunk)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    return _error(502, "invalid_stt_response")
                return STTProxyResponse.model_validate(payload)
    except _UploadLimit:
        return _error(413, "upload_too_large")
    except (httpx.HTTPError, ValueError):
        return _error(503, "stt_unavailable")


async def _proxy_websocket(socket: WebSocket) -> None:
    from websockets.exceptions import ConnectionClosed
    try:
        query = _query(socket)
        headers = service_headers()
    except (ValueError, RuntimeError):
        await socket.close(code=1008, reason="stt_unavailable")
        return
    base = urlsplit(_stt_base_url())
    uri = urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc,
                      f"{base.path}/ws/transcribe", urlencode(query), ""))
    tasks: list[asyncio.Task] = []
    accepted = False
    limit = max_upload_bytes()
    try:
        # legacy Connect is supported by the repository's websockets>=12 floor,
        # does not use system proxies, and has an explicit redirect rejection.
        async with _ServiceConnect(uri, extra_headers=headers, compression=None,
                                   max_size=limit, max_queue=8, open_timeout=10,
                                   close_timeout=2) as upstream:
            await socket.accept()
            accepted = True

            async def to_service():
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    value = message.get("bytes")
                    if value is None:
                        value = message.get("text", "")
                    size = len(value) if isinstance(value, bytes) else len(value.encode("utf-8"))
                    if size > limit:
                        await socket.close(code=1009, reason="upload_too_large")
                        return
                    await upstream.send(value)

            async def to_browser():
                async for value in upstream:
                    if isinstance(value, bytes):
                        await socket.send_bytes(value)
                    else:
                        await socket.send_text(value)

            tasks = [asyncio.create_task(to_service()), asyncio.create_task(to_browser())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                # Stop pumps before closing their upstream transport.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (ConnectionClosed, WebSocketDisconnect):
        pass
    except Exception:
        # Never echo exception strings, upstream URLs, or credential headers.
        if socket.application_state.name != "DISCONNECTED":
            await socket.close(code=1011 if accepted else 1008, reason="stt_unavailable")
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if socket.application_state.name != "DISCONNECTED":
            try:
                await socket.close(code=1000)
            except (RuntimeError, OSError):
                pass


def register_stt_proxy_routes(router: APIRouter, state: Any) -> None:
    stt_router = APIRouter(prefix="/api/stt", tags=["speech"])

    @stt_router.get("/authorityz", response_model=STTProxyResponse)
    async def authority(request: Request) -> STTProxyResponse | JSONResponse:
        return await _proxy_http(request, "/authorityz", authority=True)

    @stt_router.post("/transcribe", response_model=STTProxyResponse)
    async def transcribe(request: Request) -> STTProxyResponse | JSONResponse:
        return await _proxy_http(request, "/transcribe")

    stt_router.add_api_websocket_route("/ws/transcribe", _proxy_websocket)
    router.include_router(stt_router)
