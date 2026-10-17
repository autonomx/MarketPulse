"""The bounded webhook POST: a connection a watchdog can shut down under the request.

Kept apart from ``alerts_delivery`` so httpcore loads with the first webhook sent, not at startup.
"""

from __future__ import annotations

import socket
import ssl
import threading
from typing import Any

import httpcore
import httpx


class _RecordedStream(httpcore.NetworkStream):
    """A network stream that records its socket, so a watchdog can shut it down."""

    def __init__(self, stream: httpcore.NetworkStream, sockets: list[socket.socket]) -> None:
        self._stream = stream
        sock = stream.get_extra_info("socket")
        if isinstance(sock, socket.socket):
            sockets.append(sock)
        self._sockets = sockets

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._stream.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._stream.write(buffer, timeout)

    def close(self) -> None:
        self._stream.close()

    def start_tls(
        self, ssl_context: ssl.SSLContext, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.NetworkStream:
        return _RecordedStream(self._stream.start_tls(ssl_context, server_hostname, timeout), self._sockets)

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _RecordingBackend(httpcore.SyncBackend):
    def __init__(self) -> None:
        super().__init__()
        self.sockets: list[socket.socket] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        return _RecordedStream(super().connect_tcp(host, port, timeout, local_address, socket_options), self.sockets)


def _shut_down(sockets: list[socket.socket]) -> None:
    for sock in list(sockets):
        try:
            # shutdown, never close: it wakes a read blocked in another thread on every platform and leaves the
            # handle to its owner, so no other connection can take the handle over while that read still runs.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass  # not connected, or the plain socket a TLS socket took over


def post_within(request: httpx.Request, seconds: float) -> httpx.Response:
    """POSTs ``request`` and returns once the response headers arrive, never reading the body.

    The whole exchange is bounded: when ``seconds`` pass, a watchdog shuts the connection down under the request,
    which also ends a peer that sends its headers one byte at a time; that raises ``httpx.ReadTimeout``. A response
    that arrived is returned even if the deadline passes just after. TLS is verified against the request's
    ``sni_hostname`` extension (the connection itself goes to the address in the URL).
    """
    backend = _RecordingBackend()
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        _shut_down(backend.sockets)

    pool = httpcore.ConnectionPool(ssl_context=ssl.create_default_context(), network_backend=backend, retries=0)
    timer = threading.Timer(max(0.01, seconds), expire)
    timer.daemon = True
    timer.start()
    try:
        response = pool.handle_request(
            httpcore.Request(
                method=request.method.encode("ascii"),
                url=str(request.url),
                headers=request.headers.raw,
                content=request.content,
                extensions=dict(request.extensions),
            )
        )
        try:
            return httpx.Response(response.status, headers=response.headers, request=request)
        finally:
            response.close()
    except httpcore.ConnectTimeout as exc:
        raise httpx.ConnectTimeout(str(exc), request=request) from exc
    except httpcore.ConnectError as exc:
        if expired.is_set():
            raise httpx.ReadTimeout("send deadline", request=request) from exc
        raise httpx.ConnectError(str(exc), request=request) from exc
    except httpcore.TimeoutException as exc:
        raise httpx.ReadTimeout(str(exc), request=request) from exc
    except (httpcore.NetworkError, httpcore.ProtocolError, OSError) as exc:
        if expired.is_set():
            raise httpx.ReadTimeout("send deadline", request=request) from exc
        raise httpx.ReadError(str(exc), request=request) from exc
    finally:
        timer.cancel()
        pool.close()
