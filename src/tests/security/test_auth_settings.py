from __future__ import annotations

import pytest

from app.security.auth import (
    AUTH_ENFORCED_WHEN_UNSET,
    AuthConfigurationError,
    AuthMode,
    assert_auth_startup_allowed,
    resolve_auth_settings,
)


def test_sign_in_is_on_by_default() -> None:
    # WP-4.1: the owner approved the default flip (2026-10-06).
    assert AUTH_ENFORCED_WHEN_UNSET is True
    settings = resolve_auth_settings({})
    assert settings.mode is AuthMode.LOCAL
    assert settings.explicit is False
    assert settings.enforced is True


def test_account_policy_defaults_follow_the_deployment() -> None:
    development = resolve_auth_settings({}).accounts
    assert (development.registration, development.guests, development.google) == ("open", True, None)
    production = resolve_auth_settings({"OMNIX_ENV": "production"}).accounts
    assert (production.registration, production.guests) == ("invite", False)
    opened = resolve_auth_settings({"OMNIX_ENV": "production", "OMNIX_AUTH_REGISTRATION": "open",
                                    "OMNIX_AUTH_GUESTS": "false", "OMNIX_AUTH_REMEMBER_DAYS": "14"}).accounts
    assert (opened.registration, opened.guests, opened.remember_ttl_seconds) == ("open", False, 14 * 86400)
    with pytest.raises(AuthConfigurationError):
        resolve_auth_settings({"OMNIX_AUTH_REGISTRATION": "everyone"})
    # Accounts belong to local mode; an organization's IdP decides in OIDC mode.
    assert resolve_auth_settings({"OMNIX_AUTH_MODE": "disabled"}).accounts.registration == "invite"


def test_google_sign_in_needs_its_whole_configuration() -> None:
    with pytest.raises(AuthConfigurationError, match="OMNIX_GOOGLE_CLIENT_SECRET"):
        resolve_auth_settings({"OMNIX_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com"})
    google = resolve_auth_settings({
        "OMNIX_GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
        "OMNIX_GOOGLE_CLIENT_SECRET": "secret",
        "OMNIX_GOOGLE_REDIRECT_URI": "http://127.0.0.1:8080/api/auth/google/callback",
    }).accounts.google
    assert google is not None and google.issuer == "https://accounts.google.com"
    assert google.scopes == ("openid", "email", "profile")


@pytest.mark.parametrize("value", ["local", "LOCAL", " local "])
def test_explicit_local_mode_enforces_authentication(value: str) -> None:
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": value})
    assert settings.mode is AuthMode.LOCAL
    assert settings.enforced is True


def test_unknown_mode_fails_closed() -> None:
    with pytest.raises(AuthConfigurationError):
        resolve_auth_settings({"OMNIX_AUTH_MODE": "none"})


def test_session_lifetimes_default_to_twelve_hours_idle_and_seven_days_absolute() -> None:
    settings = resolve_auth_settings({"OMNIX_AUTH_MODE": "local"})
    assert settings.sliding_ttl_seconds == 12 * 3600
    assert settings.absolute_ttl_seconds == 7 * 86400


@pytest.mark.parametrize(
    ("env", "bind_host"),
    [
        ({"OMNIX_ENV": "test"}, "0.0.0.0"),
        ({"OMNIX_ENV": "development"}, "127.0.0.1"),
        ({"OMNIX_ENV": "development"}, "::1"),
        ({"OMNIX_ENV": "development"}, "localhost"),
    ],
)
def test_disabled_mode_is_allowed_only_for_tests_or_loopback_development(env, bind_host) -> None:
    source = {"OMNIX_AUTH_MODE": "disabled", **env}
    assert_auth_startup_allowed(resolve_auth_settings(source), bind_host=bind_host, env=source)


@pytest.mark.parametrize(
    ("env", "bind_host"),
    [
        ({"OMNIX_ENV": "development"}, "0.0.0.0"),
        ({"OMNIX_ENV": "development"}, None),
        ({"OMNIX_ENV": "production"}, "127.0.0.1"),
        ({}, "192.168.1.20"),
    ],
)
def test_disabled_mode_refuses_to_start_elsewhere(env, bind_host) -> None:
    source = {"OMNIX_AUTH_MODE": "disabled", **env}
    settings = resolve_auth_settings(source)
    assert settings.enforced is False
    with pytest.raises(AuthConfigurationError):
        assert_auth_startup_allowed(settings, bind_host=bind_host, env=source)


def test_oidc_mode_requires_issuer_client_and_redirect() -> None:
    with pytest.raises(AuthConfigurationError):
        resolve_auth_settings({"OMNIX_AUTH_MODE": "oidc", "OMNIX_OIDC_ISSUER": "https://idp.example"})


def test_oidc_issuer_must_use_https_off_loopback() -> None:
    with pytest.raises(AuthConfigurationError):
        resolve_auth_settings(
            {
                "OMNIX_AUTH_MODE": "oidc",
                "OMNIX_OIDC_ISSUER": "http://idp.example",
                "OMNIX_OIDC_CLIENT_ID": "omnix",
                "OMNIX_OIDC_REDIRECT_URI": "https://omnix.example/api/auth/oidc/callback",
            }
        )


@pytest.mark.parametrize("role", ["owner", "admin", "approver"])
def test_oidc_just_in_time_provisioning_never_mints_privileged_roles(role: str) -> None:
    with pytest.raises(AuthConfigurationError):
        resolve_auth_settings(
            {
                "OMNIX_AUTH_MODE": "oidc",
                "OMNIX_OIDC_ISSUER": "https://idp.example",
                "OMNIX_OIDC_CLIENT_ID": "omnix",
                "OMNIX_OIDC_REDIRECT_URI": "https://omnix.example/api/auth/oidc/callback",
                "OMNIX_OIDC_DEFAULT_ROLE": role,
            }
        )


def test_oidc_settings_resolve() -> None:
    settings = resolve_auth_settings(
        {
            "OMNIX_AUTH_MODE": "oidc",
            "OMNIX_OIDC_ISSUER": "https://idp.example/",
            "OMNIX_OIDC_CLIENT_ID": "omnix",
            "OMNIX_OIDC_REDIRECT_URI": "https://omnix.example/api/auth/oidc/callback",
            "OMNIX_OIDC_ALLOWED_DOMAINS": "Example.com, @corp.example",
        }
    )
    assert settings.enforced is True
    assert settings.oidc is not None
    assert settings.oidc.issuer == "https://idp.example"
    assert settings.oidc.allowed_domains == ("example.com", "corp.example")
    assert settings.oidc.default_role == "member"
