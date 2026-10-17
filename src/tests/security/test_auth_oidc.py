from __future__ import annotations

import time

import jwt
import pytest

from app.security.auth.oidc import safe_redirect_path
from tests.support.fake_oidc import API_AUDIENCE, CLIENT_ID, FakeIdentityProvider, oidc_settings


@pytest.fixture
def idp() -> FakeIdentityProvider:
    return FakeIdentityProvider()


def _id_claims(**overrides):
    return {"sub": "alice", "aud": CLIENT_ID, "nonce": "n-1", **overrides}


def test_valid_id_token_is_accepted(idp) -> None:
    claims = idp.client().validate_id_token(idp.sign(_id_claims()), nonce="n-1")
    assert claims is not None and claims["sub"] == "alice"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.example"},
        {"nonce": "replayed"},
        {"exp": int(time.time()) - 3600},
        {"aud": [CLIENT_ID, "other"], "azp": "other"},
        {"azp": "other"},
    ],
)
def test_id_token_claim_violations_are_rejected(idp, overrides) -> None:
    assert idp.client().validate_id_token(idp.sign(_id_claims(**overrides)), nonce="n-1") is None


def test_unsigned_and_symmetric_tokens_are_rejected(idp) -> None:
    client = idp.client()
    payload = {"iss": "https://idp.example", "iat": int(time.time()), "exp": int(time.time()) + 60, **_id_claims()}
    unsigned = jwt.encode(payload, key=None, algorithm="none", headers={"kid": "key-1"})
    assert client.validate_id_token(unsigned, nonce="n-1") is None
    # HS256 with the public client id as the "secret" is a classic confusion attack.
    symmetric = jwt.encode(payload, key=CLIENT_ID * 4, algorithm="HS256", headers={"kid": "key-1"})
    assert client.validate_id_token(symmetric, nonce="n-1") is None


def test_token_signed_by_unknown_key_is_rejected(idp) -> None:
    client = idp.client()
    stranger = FakeIdentityProvider()
    assert client.validate_id_token(stranger.sign(_id_claims()), nonce="n-1") is None


def test_unknown_kid_refreshes_jwks_once_per_interval(idp) -> None:
    now = {"value": 1000.0}
    client = idp.client(clock=lambda: now["value"])
    assert client.validate_id_token(idp.sign(_id_claims()), nonce="n-1") is not None
    assert idp.jwks_fetches == 1

    idp.add_key("key-2")
    rotated = idp.sign(_id_claims(), kid="key-2")
    # Within the refresh interval, a new kid does not trigger a fetch.
    now["value"] += 5
    assert client.validate_id_token(rotated, nonce="n-1") is None
    assert idp.jwks_fetches == 1
    now["value"] += 60
    assert client.validate_id_token(rotated, nonce="n-1") is not None
    assert idp.jwks_fetches == 2


def test_access_tokens_need_the_api_audience(idp) -> None:
    client = idp.client()
    assert client.validate_access_token(idp.sign({"sub": "alice", "aud": API_AUDIENCE})) is not None
    assert client.validate_access_token(idp.sign({"sub": "alice", "aud": CLIENT_ID})) is None
    without_api = idp.client(oidc_settings(api_audience=None))
    assert without_api.validate_access_token(idp.sign({"sub": "alice", "aud": API_AUDIENCE})) is None


def test_authorization_code_flow_uses_pkce_and_binds_the_nonce(idp) -> None:
    client = idp.client()
    start = client.begin(redirect_after="/chat?x=1")
    assert "code_challenge_method=S256" in start.authorization_url
    assert start.redirect_after == "/chat?x=1"
    code, state = idp.authorize(start.authorization_url, {"sub": "alice"})
    assert state == start.state
    claims = client.exchange_code(code=code, code_verifier=start.code_verifier, nonce=start.nonce)
    assert claims is not None and claims["sub"] == "alice"
    # A wrong verifier fails at the token endpoint.
    code, _ = idp.authorize(start.authorization_url, {"sub": "alice"})
    assert client.exchange_code(code=code, code_verifier="wrong", nonce=start.nonce) is None


def test_admission_requires_verified_allowed_domain_and_group(idp) -> None:
    client = idp.client(oidc_settings(allowed_domains=("example.com",), required_group="omnix"))
    allowed = client.admission(
        {"sub": "a", "email": "Alice@Example.com", "email_verified": True, "groups": ["omnix"], "name": "Alice"}
    )
    assert allowed.allowed and allowed.email == "alice@example.com" and allowed.display_name == "Alice"
    assert client.admission({"sub": "a", "email": "alice@example.com", "groups": ["omnix"]}).reason == "verified_email_required"
    assert (
        client.admission({"sub": "a", "email": "eve@evil.example", "email_verified": True, "groups": ["omnix"]}).reason
        == "email_domain_not_allowed"
    )
    assert (
        client.admission({"sub": "a", "email": "alice@example.com", "email_verified": True, "groups": []}).reason
        == "required_group_missing"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/chat", "/chat"),
        ("/chat?x=1#y", "/chat?x=1#y"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
        ("/ok\r\nSet-Cookie: x", "/"),
        (None, "/"),
    ],
)
def test_post_login_redirects_stay_on_this_origin(value, expected) -> None:
    assert safe_redirect_path(value) == expected
