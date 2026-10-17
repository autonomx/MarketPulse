"""Centralized outbound-web policy for research retrieval."""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

_ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml"}
_ALLOWED_PORTS = {80, 443}


class OutboundWebPolicyError(RuntimeError):
    """Raised when a research fetch violates outbound-web policy."""


@dataclass(slots=True)
class OutboundWebResponse:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    content: bytes
    redirect_count: int
    elapsed_ms: int


Resolver = Callable[[str, int], Iterable[str]]


class OutboundWebPolicy:
    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        client: httpx.Client | None = None,
        total_timeout_seconds: float = 8.0,
        max_redirects: int = 5,
        max_compressed_bytes: int = 2_000_000,
        max_decompressed_bytes: int = 4_000_000,
        allowed_ports: set[int] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.resolver = resolver or resolve_host_addresses
        self.client = client
        self.total_timeout_seconds = max(0.1, float(total_timeout_seconds))
        self.max_redirects = max(0, min(10, int(max_redirects)))
        self.max_compressed_bytes = max(1024, int(max_compressed_bytes))
        self.max_decompressed_bytes = max(self.max_compressed_bytes, int(max_decompressed_bytes))
        self.allowed_ports = allowed_ports or set(_ALLOWED_PORTS)
        self.monotonic = monotonic

    def validate_url(self, url: str) -> str:
        text = str(url or "").strip()
        try:
            parsed = urlsplit(text)
            port = parsed.port
        except ValueError as exc:
            raise OutboundWebPolicyError("invalid_url") from exc
        if parsed.scheme.lower() not in {"http", "https"}:
            raise OutboundWebPolicyError("unsupported_url_scheme")
        if parsed.username is not None or parsed.password is not None:
            raise OutboundWebPolicyError("url_userinfo_not_allowed")
        hostname = (parsed.hostname or "").strip().rstrip(".").lower()
        if not hostname:
            raise OutboundWebPolicyError("missing_url_hostname")
        effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        if effective_port not in self.allowed_ports:
            raise OutboundWebPolicyError("url_port_not_allowed")
        self._validate_hostname(hostname, effective_port)
        return text

    def fetch(self, url: str) -> OutboundWebResponse:
        started = self.monotonic()
        current_url = self.validate_url(url)
        redirects = 0
        client = self.client or httpx.Client(follow_redirects=False, verify=True)
        close_client = self.client is None
        try:
            while True:
                remaining = self.total_timeout_seconds - (self.monotonic() - started)
                if remaining <= 0:
                    raise OutboundWebPolicyError("outbound_total_timeout")
                self.validate_url(current_url)
                response = client.get(
                    current_url,
                    timeout=remaining,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
                        "User-Agent": "OmnixResearch/1.0",
                    },
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("location") or "").strip()
                    if not location:
                        raise OutboundWebPolicyError("redirect_without_location")
                    redirects += 1
                    if redirects > self.max_redirects:
                        raise OutboundWebPolicyError("redirect_limit_exceeded")
                    current_url = self.validate_url(urljoin(current_url, location))
                    continue
                response.raise_for_status()
                content_length = _header_int(response.headers.get("content-length"))
                if content_length is not None and content_length > self.max_compressed_bytes:
                    raise OutboundWebPolicyError("compressed_response_too_large")
                content_type = _content_type(response.headers.get("content-type"))
                if content_type not in _ALLOWED_CONTENT_TYPES:
                    raise OutboundWebPolicyError("unsupported_response_content_type")
                content = response.content
                if len(content) > self.max_decompressed_bytes:
                    raise OutboundWebPolicyError("decompressed_response_too_large")
                if b"\x00" in content[:4096]:
                    raise OutboundWebPolicyError("response_content_type_mismatch")
                return OutboundWebResponse(
                    requested_url=url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    content_type=content_type,
                    content=content,
                    redirect_count=redirects,
                    elapsed_ms=round((self.monotonic() - started) * 1000),
                )
        except httpx.TimeoutException as exc:
            raise OutboundWebPolicyError("outbound_total_timeout") from exc
        except httpx.HTTPError as exc:
            raise OutboundWebPolicyError(f"outbound_http_error:{type(exc).__name__}") from exc
        finally:
            if close_client:
                client.close()

    def _validate_hostname(self, hostname: str, port: int) -> None:
        literal = _parse_ip_literal(hostname)
        addresses = [str(literal)] if literal is not None else list(self.resolver(hostname, port))
        if not addresses:
            raise OutboundWebPolicyError("hostname_did_not_resolve")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as exc:
                raise OutboundWebPolicyError("invalid_resolved_address") from exc
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                raise OutboundWebPolicyError("non_public_address_blocked")


def resolve_host_addresses(hostname: str, port: int) -> list[str]:
    try:
        rows = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise OutboundWebPolicyError("hostname_resolution_failed") from exc
    return sorted({str(row[4][0]) for row in rows})


def _parse_ip_literal(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    candidate = hostname.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _content_type(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _header_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except (TypeError, ValueError):
        return None
