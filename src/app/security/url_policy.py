"""Outbound URL policy for configurable endpoints (WP-4.10).

Provider base URLs and similar settings name servers Omnix will call. The
policy keeps them from reaching what a request should never reach:

- schemes: http and https only, with a host and no credentials;
- always blocked: link-local addresses (169.254.0.0/16, fe80::/10, which hold
  cloud metadata services), metadata hostnames, unspecified, multicast and
  reserved addresses;
- loopback is allowed (local model servers);
- private networks: the CIDRs in ``OMNIX_ALLOWED_PRIVATE_NETWORKS``
  (comma-separated). Unset, they are allowed while sign-in is off (one local
  user configuring their own LAN servers) and refused when sign-in is on;
- public addresses are allowed.

``check_outbound_url`` runs when a setting is saved and, with
``resolve=True``, before each connection so a hostname that later resolves to
a blocked address is refused.

Destinations users type for the server to call on their behalf (alert
webhooks, TVP-0.5a) use ``strict``: every address that is not globally
routable (loopback, private, shared 100.64.0.0/10, ...) is refused unless
``OMNIX_ALLOWED_PRIVATE_NETWORKS`` names it, whether sign-in is on or off. ``outbound_addresses`` resolves and checks a host once and returns the
addresses, so the caller connects to one of them instead of resolving again (a
hostname cannot rebind to a blocked address between the check and the
connection).
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from app.config.env import env_str

METADATA_HOSTNAMES = frozenset({
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.com",
    "instance-data",
    "instance-data.ec2.internal",
})

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Iterable[str]]


class UrlPolicyError(ValueError):
    """The URL may not be used as an outbound endpoint."""


def resolve_hostname(hostname: str, port: int) -> list[str]:
    """Every address ``hostname`` resolves to, in the system's preference order (the default ``Resolver``)."""
    try:
        rows = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlPolicyError("hostname_resolution_failed") from exc
    return list(dict.fromkeys(str(row[4][0]) for row in rows))


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _configured_private_networks() -> list[Network] | None:
    """``OMNIX_ALLOWED_PRIVATE_NETWORKS``; ``None`` when unset."""
    configured = (env_str("OMNIX_ALLOWED_PRIVATE_NETWORKS", "") or "").strip()
    if not configured:
        return None
    try:
        return [ipaddress.ip_network(item.strip(), strict=False) for item in configured.split(",") if item.strip()]
    except ValueError as exc:
        raise UrlPolicyError("OMNIX_ALLOWED_PRIVATE_NETWORKS must be comma-separated CIDRs") from exc


def allowed_private_networks() -> list[Network] | None:
    """Configured private networks; ``None`` means every private network."""
    configured = _configured_private_networks()
    if configured is not None:
        return configured
    from app.security.auth.settings import resolve_auth_settings

    return [] if resolve_auth_settings().enforced else None


def check_address(address: IPAddress, *, strict: bool = False) -> None:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_link_local:
        raise UrlPolicyError("link_local_address_blocked")
    if strict and not address.is_global:
        # Only networks the operator named; never everything because sign-in is off. Not-global also covers
        # shared address space (100.64.0.0/10: carrier NAT, Tailscale, some cloud metadata services).
        configured = _configured_private_networks() or []
        if any(address in network for network in configured):
            return
        if address.is_loopback:
            raise UrlPolicyError("loopback_address_not_allowed")
        if address.is_private:
            raise UrlPolicyError("private_address_not_allowed")
        raise UrlPolicyError("non_global_address_not_allowed")
    if address.is_loopback:
        return
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise UrlPolicyError("reserved_address_blocked")
    if address.is_private:
        networks = allowed_private_networks()
        if networks is None or any(address in network for network in networks):
            return
        raise UrlPolicyError("private_address_not_allowed")


def check_outbound_url(
    url: str,
    *,
    resolve: bool = False,
    resolver: Resolver = resolve_hostname,
    strict: bool = False,
    https_only: bool = False,
) -> str:
    """Validate ``url``; with ``resolve``, also every address its host resolves to."""
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme not in ({"https"} if https_only else {"http", "https"}):
        raise UrlPolicyError("url_scheme_not_allowed")
    if parsed.username or parsed.password:
        raise UrlPolicyError("url_credentials_not_allowed")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise UrlPolicyError("url_host_required")
    if hostname in METADATA_HOSTNAMES:
        raise UrlPolicyError("metadata_host_blocked")
    try:
        literal: IPAddress | None = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        check_address(literal, strict=strict)
    elif resolve:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = list(resolver(hostname, port))
        except UrlPolicyError:
            # A name that does not resolve reaches nothing; the connection
            # itself reports the failure.
            return url
        for value in addresses:
            check_address(ipaddress.ip_address(value), strict=strict)
    return url


def outbound_addresses(url: str, *, strict: bool = False, resolver: Resolver = resolve_hostname) -> list[IPAddress]:
    """The checked addresses a connection to ``url`` may use; connect to one of them, never resolve again.

    Raises ``UrlPolicyError("hostname_resolution_failed")`` when the host does not resolve, and the
    policy's error when the URL or any resolved address is not allowed (one blocked address refuses
    the host: a name that also points somewhere internal is not trusted).
    """
    check_outbound_url(url, strict=strict)
    parsed = urlsplit(str(url or "").strip())
    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = [ipaddress.ip_address(value) for value in resolver(hostname, port)]
    if not addresses:
        raise UrlPolicyError("hostname_resolution_failed")
    for address in addresses:
        check_address(address, strict=strict)
    return addresses


__all__ = [
    "METADATA_HOSTNAMES",
    "UrlPolicyError",
    "allowed_private_networks",
    "check_address",
    "check_outbound_url",
    "outbound_addresses",
    "resolve_hostname",
]
