"""A local OpenID provider: real RSA signatures, JWKS and a token endpoint."""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.security.auth.oidc import OidcClient
from app.security.auth.settings import OidcSettings

ISSUER = "https://idp.example"
CLIENT_ID = "omnix-web"
API_AUDIENCE = "omnix-api"
REDIRECT_URI = "https://omnix.example/api/auth/oidc/callback"


def oidc_settings(**overrides: Any) -> OidcSettings:
    values: dict[str, Any] = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": None,
        "redirect_uri": REDIRECT_URI,
        "scopes": ("openid", "email", "profile"),
        "api_audience": API_AUDIENCE,
        "allowed_domains": (),
        "required_group": None,
        "groups_claim": "groups",
        "workspace_id": "workspace:local",
        "default_role": "member",
    }
    values.update(overrides)
    return OidcSettings(**values)


class FakeIdentityProvider:
    def __init__(self) -> None:
        self.keys: dict[str, Any] = {}
        self.add_key("key-1")
        self.jwks_fetches = 0
        self.token_requests: list[dict[str, str]] = []
        self.pending: dict[str, dict[str, Any]] = {}

    def add_key(self, kid: str) -> None:
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        keys = []
        for kid, key in self.keys.items():
            entry = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            entry.update({"kid": kid, "use": "sig", "alg": "RS256"})
            keys.append(entry)
        return {"keys": keys}

    def sign(self, claims: dict[str, Any], *, kid: str = "key-1", algorithm: str = "RS256") -> str:
        now = int(time.time())
        payload = {"iss": ISSUER, "iat": now, "exp": now + 300, **claims}
        return jwt.encode(payload, self.keys[kid], algorithm=algorithm, headers={"kid": kid})

    def http_get(self, url: str) -> dict[str, Any]:
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            }
        if url == f"{ISSUER}/jwks":
            self.jwks_fetches += 1
            return self.jwks()
        raise AssertionError(f"unexpected IdP request {url}")

    def authorize(self, authorization_url: str, claims: dict[str, Any]) -> tuple[str, str]:
        """Simulate the user signing in; returns (code, state) for the callback."""
        query = parse_qs(urlsplit(authorization_url).query)
        code = f"code-{len(self.pending)}"
        self.pending[code] = {
            "claims": {"aud": CLIENT_ID, "nonce": query["nonce"][0], **claims},
            "challenge": query["code_challenge"][0],
        }
        return code, query["state"][0]

    def http_post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        assert url == f"{ISSUER}/token"
        self.token_requests.append(form)
        grant = self.pending.pop(form["code"], None)
        if grant is None:
            raise ValueError("invalid_grant")
        from app.security.auth.oidc import _pkce_challenge

        if _pkce_challenge(form["code_verifier"]) != grant["challenge"]:
            raise ValueError("invalid_grant")
        return {"id_token": self.sign(grant["claims"]), "token_type": "Bearer"}

    def client(self, settings: OidcSettings | None = None, **kwargs: Any) -> OidcClient:
        return OidcClient(
            settings or oidc_settings(),
            http_get=self.http_get,
            http_post_form=self.http_post_form,
            **kwargs,
        )
