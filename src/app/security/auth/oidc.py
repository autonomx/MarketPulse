"""OpenID Connect Authorization Code + PKCE client (WP-4.1).

ID tokens and bearer access tokens are verified with PyJWT against the
issuer's JWKS. Keys are cached and refreshed when an unknown ``kid`` appears,
at most once per refresh interval so forged ``kid`` values cannot force a
fetch per request.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import base64
import hashlib
import logging
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

from .settings import OidcSettings

logger = logging.getLogger(__name__)

# Asymmetric algorithms only: "none" and HMAC algorithms would let anyone who
# knows the client id (or nobody at all) mint tokens.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
_JWKS_MIN_REFRESH_SECONDS = 30.0
_JWKS_MAX_AGE_SECONDS = 3600.0
_HTTP_TIMEOUT_SECONDS = 10.0
_LEEWAY_SECONDS = 60


@dataclass(frozen=True, slots=True)
class OidcLoginStart:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str
    browser_binding: str
    redirect_after: str


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    reason: str
    email: str | None = None
    display_name: str = ""


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def safe_redirect_path(value: str | None) -> str:
    """Only same-origin absolute paths; anything else becomes ``/``."""
    candidate = (value or "/").strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(char) < 0x20 for char in candidate)
        or len(candidate) > 2048
    ):
        return "/"
    return candidate


class OidcClient:
    def __init__(
        self,
        settings: OidcSettings,
        *,
        http_get: Callable[[str], dict[str, Any]] | None = None,
        http_post_form: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._http_get = http_get or self._default_get
        self._http_post_form = http_post_form or self._default_post_form
        self._clock = clock
        self._lock = threading.Lock()
        self._metadata: dict[str, Any] | None = None
        self._keys: dict[str, PyJWK] = {}
        self._keys_fetched_at: float | None = None

    # HTTP -------------------------------------------------------------------

    @staticmethod
    def _default_get(url: str) -> dict[str, Any]:
        response = httpx.get(url, timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("oidc_response_not_object")
        return payload

    @staticmethod
    def _default_post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
        response = httpx.post(url, data=form, timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("oidc_response_not_object")
        return payload

    # Discovery and keys -----------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            if self._metadata is None:
                document = self._http_get(f"{self.settings.issuer}/.well-known/openid-configuration")
                if str(document.get("issuer", "")).rstrip("/") != self.settings.issuer:
                    raise ValueError("oidc_issuer_mismatch")
                for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not isinstance(document.get(field), str) or not document[field]:
                        raise ValueError(f"oidc_metadata_missing_{field}")
                self._metadata = document
            return self._metadata

    def _refresh_keys_locked(self, jwks_uri: str) -> None:
        document = self._http_get(jwks_uri)
        keys: dict[str, PyJWK] = {}
        for entry in document.get("keys") or []:
            if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            try:
                keys[kid] = PyJWK.from_dict(entry)
            except jwt.PyJWTError:
                logger.warning("oidc_jwk_unusable", extra={"kid": kid})
        self._keys = keys
        self._keys_fetched_at = self._clock()

    def _key_for(self, kid: str) -> PyJWK | None:
        jwks_uri = str(self.metadata()["jwks_uri"])
        with self._lock:
            now = self._clock()
            stale = self._keys_fetched_at is None or now - self._keys_fetched_at > _JWKS_MAX_AGE_SECONDS
            if stale:
                self._refresh_keys_locked(jwks_uri)
            elif kid not in self._keys and now - (self._keys_fetched_at or 0.0) >= _JWKS_MIN_REFRESH_SECONDS:
                self._refresh_keys_locked(jwks_uri)
            return self._keys.get(kid)

    def _decode(self, token: str, *, audience: str) -> dict[str, Any] | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            return None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            return None
        key = self._key_for(kid)
        if key is None:
            return None
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=audience,
                issuer=self.settings.issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            return None
        return claims if isinstance(claims, dict) else None

    # Login ----------------------------------------------------------------

    def begin(self, *, redirect_after: str | None) -> OidcLoginStart:
        metadata = self.metadata()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "scope": " ".join(self.settings.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        endpoint = str(metadata["authorization_endpoint"])
        separator = "&" if "?" in endpoint else "?"
        return OidcLoginStart(
            authorization_url=f"{endpoint}{separator}{query}",
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            browser_binding=secrets.token_urlsafe(32),
            redirect_after=safe_redirect_path(redirect_after),
        )

    def exchange_code(self, *, code: str, code_verifier: str, nonce: str) -> dict[str, Any] | None:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.client_id,
            "code_verifier": code_verifier,
        }
        if self.settings.client_secret:
            form["client_secret"] = self.settings.client_secret
        try:
            response = self._http_post_form(str(self.metadata()["token_endpoint"]), form)
        except (httpx.HTTPError, ValueError):
            logger.warning("oidc_token_exchange_failed")
            return None
        id_token = response.get("id_token")
        if not isinstance(id_token, str):
            return None
        return self.validate_id_token(id_token, nonce=nonce)

    def validate_id_token(self, token: str, *, nonce: str) -> dict[str, Any] | None:
        claims = self._decode(token, audience=self.settings.client_id)
        if claims is None:
            return None
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            return None
        audiences = claims.get("aud")
        multiple = isinstance(audiences, list) and len(audiences) > 1
        azp = claims.get("azp")
        if (multiple or azp is not None) and azp != self.settings.client_id:
            return None
        return claims

    def validate_access_token(self, token: str) -> dict[str, Any] | None:
        if not self.settings.api_audience:
            return None
        return self._decode(token, audience=self.settings.api_audience)

    def admission(self, claims: dict[str, Any]) -> AdmissionDecision:
        email_value = claims.get("email")
        email = email_value.strip().lower() if isinstance(email_value, str) and email_value.strip() else None
        if self.settings.allowed_domains:
            if email is None or claims.get("email_verified") is not True:
                return AdmissionDecision(False, "verified_email_required")
            domain = email.rsplit("@", 1)[-1]
            if domain not in self.settings.allowed_domains:
                return AdmissionDecision(False, "email_domain_not_allowed")
        if self.settings.required_group:
            groups = claims.get(self.settings.groups_claim)
            if not isinstance(groups, list) or self.settings.required_group not in groups:
                return AdmissionDecision(False, "required_group_missing")
        name = next(
            (
                str(claims[key]).strip()
                for key in ("name", "preferred_username", "email")
                if isinstance(claims.get(key), str) and str(claims[key]).strip()
            ),
            "OIDC user",
        )
        return AdmissionDecision(True, "admitted", email=email, display_name=name[:200])
