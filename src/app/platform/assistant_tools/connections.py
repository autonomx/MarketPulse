"""Assistant tool provider connection discovery."""
from __future__ import annotations

from app.config.env import env_str, environment

import os
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel

from .config_store import load_assistant_tools_config, save_assistant_tools_config
from .credentials import (
    AssistantToolCredentialRecord,
    AssistantToolOAuthClientRecord,
    credential_for_tool,
    delete_tool_credential,
    expires_at_from_now,
    is_expired,
    oauth_client_for_provider,
    upsert_oauth_client,
    upsert_tool_credential,
)


class AssistantToolConnectionStartPayload(BaseModel):
    tool_id: str
    provider: str | None = None
    configured: bool = False
    auth_url: str | None = None
    redirect_uri: str | None = None
    message: str = ""


class AssistantToolConnectionCompletePayload(BaseModel):
    tool_id: str
    provider: str
    connected: bool = False
    account_label: str | None = None
    account_email: str | None = None
    message: str = ""


class AssistantToolDisconnectPayload(BaseModel):
    tool_id: str
    disconnected: bool = True
    revoked_at_provider: bool = False
    message: str = ""


class AssistantToolOAuthClientPayload(BaseModel):
    client_id: str = ""
    client_secret: str = ""


GOOGLE_TOOL_IDS = {"gmail", "calendar", "contacts"}
_OAUTH_STATE_TTL_SECONDS = 600.0
_MAX_PENDING_OAUTH_STATES = 2048
_PENDING_OAUTH_LOCK = threading.RLock()
_PENDING_OAUTH_STATES: dict[str, tuple[str, str, float]] = {}


class AssistantToolConnectionError(RuntimeError):
    pass


def google_access_token_for_tool(tool_id: str) -> str:
    """Return a usable Google token, refreshing the persisted credential when needed."""

    _load_local_env()
    credential = credential_for_tool(tool_id)
    if credential is None or credential.provider.lower() != "google":
        raise AssistantToolConnectionError(f"Google {tool_id} is not connected.")
    if credential.access_token and not is_expired(credential.expires_at):
        return credential.access_token
    if not credential.refresh_token:
        raise AssistantToolConnectionError(f"Google {tool_id} needs to be reconnected.")
    client_id, client_secret = _oauth_client_credentials("google")
    if not client_id or not client_secret:
        raise AssistantToolConnectionError("Google OAuth client credentials are unavailable.")
    token_payload = _post_form_json(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
        },
    )
    access_token = _safe_str(token_payload.get("access_token"))
    if not access_token:
        raise AssistantToolConnectionError(f"Google {tool_id} token refresh failed.")
    scopes = _safe_str(token_payload.get("scope")).split() or credential.scopes
    upsert_tool_credential(
        credential.model_copy(
            update={
                "access_token": access_token,
                "token_type": _safe_str(token_payload.get("token_type")) or credential.token_type,
                "scopes": scopes,
                "expires_at": expires_at_from_now(token_payload.get("expires_in")),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    return access_token


def assistant_tool_connection_start_payload(tool_id: str, request_base_url: str | None = None) -> AssistantToolConnectionStartPayload:
    _load_local_env()
    if tool_id in GOOGLE_TOOL_IDS:
        return _google_connection_start_payload(tool_id, request_base_url)
    if tool_id == "github":
        return _github_connection_start_payload(tool_id, request_base_url)
    return AssistantToolConnectionStartPayload(
        tool_id=tool_id,
        message=f"No account connection provider is registered for {tool_id}.",
    )


def save_assistant_tool_oauth_client(tool_id: str, payload: AssistantToolOAuthClientPayload, request_base_url: str | None = None) -> AssistantToolConnectionStartPayload:
    _load_local_env()
    provider = _provider_for_tool(tool_id)
    if provider not in {"google", "github"}:
        return AssistantToolConnectionStartPayload(
            tool_id=tool_id,
            message=f"No account connection provider is registered for {tool_id}.",
        )
    client_id = payload.client_id.strip()
    client_secret = payload.client_secret.strip()
    if not client_id or not client_secret:
        return AssistantToolConnectionStartPayload(
            tool_id=tool_id,
            provider=provider.title(),
            redirect_uri=_oauth_redirect_uri(provider, request_base_url),
            message=f"{provider.title()} OAuth app credentials need both a client ID and client secret.",
        )
    upsert_oauth_client(
        AssistantToolOAuthClientRecord(
            provider=provider,
            client_id=client_id,
            client_secret=client_secret,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    return assistant_tool_connection_start_payload(tool_id, request_base_url)


def _google_connection_start_payload(tool_id: str, request_base_url: str | None = None) -> AssistantToolConnectionStartPayload:
    client_id, _client_secret = _oauth_client_credentials("google")
    redirect_uri = _oauth_redirect_uri("google", request_base_url)
    if not client_id:
        return AssistantToolConnectionStartPayload(
            tool_id=tool_id,
            provider="Google",
            redirect_uri=redirect_uri,
            message=f"Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET on the backend. Authorized redirect URI: {redirect_uri}",
        )
    params = {
        "access_type": "offline",
        "client_id": client_id,
        "include_granted_scopes": "true",
        "prompt": "consent",
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_google_scopes_for_tool(tool_id)),
        "state": _issue_oauth_state("google", tool_id),
    }
    return AssistantToolConnectionStartPayload(
        tool_id=tool_id,
        provider="Google",
        configured=True,
        auth_url=f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}",
        redirect_uri=redirect_uri,
        message="Open Google to sign in and grant access.",
    )


def _google_scopes_for_tool(tool_id: str) -> list[str]:
    if tool_id == "gmail":
        return [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
    if tool_id == "calendar":
        return [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/calendar.events",
        ]
    return [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/contacts.readonly",
    ]


def _github_connection_start_payload(tool_id: str, request_base_url: str | None = None) -> AssistantToolConnectionStartPayload:
    client_id, _client_secret = _oauth_client_credentials("github")
    redirect_uri = _oauth_redirect_uri("github", request_base_url)
    if not client_id:
        return AssistantToolConnectionStartPayload(
            tool_id=tool_id,
            provider="GitHub",
            redirect_uri=redirect_uri,
            message=f"GitHub OAuth is not configured. Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET on the backend. Authorized redirect URI: {redirect_uri}",
        )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "repo read:user user:email",
        "state": _issue_oauth_state("github", tool_id),
    }
    return AssistantToolConnectionStartPayload(
        tool_id=tool_id,
        provider="GitHub",
        configured=True,
        auth_url=f"https://github.com/login/oauth/authorize?{urlencode(params)}",
        redirect_uri=redirect_uri,
        message="Open GitHub to sign in and grant access.",
    )


def complete_google_connection(code: str, state: str, request_base_url: str | None = None) -> AssistantToolConnectionCompletePayload:
    _load_local_env()
    tool_id = _consume_oauth_state("google", state)
    provider = "Google"
    if tool_id not in GOOGLE_TOOL_IDS:
        return AssistantToolConnectionCompletePayload(
            tool_id="calendar",
            provider=provider,
            message="Google OAuth state is invalid or expired. Start the connection again.",
        )
    client_id, client_secret = _oauth_client_credentials("google")
    redirect_uri = _oauth_redirect_uri("google", request_base_url)
    if not client_id or not client_secret or not redirect_uri:
        return AssistantToolConnectionCompletePayload(
            tool_id=tool_id,
            provider=provider,
            message="Google OAuth callback is not configured. Set GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and OMNIX_ASSISTANT_TOOLS_GOOGLE_REDIRECT_URI.",
        )
    token_payload = _post_form_json(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        return AssistantToolConnectionCompletePayload(tool_id=tool_id, provider=provider, message="Google did not return an access token.")
    profile = _get_bearer_json("https://www.googleapis.com/oauth2/v2/userinfo", access_token)
    email = _safe_str(profile.get("email"))
    label = _safe_str(profile.get("name")) or email
    _save_connected_account(tool_id, label, email)
    _save_connected_credential(tool_id, provider, token_payload, label, email)
    return AssistantToolConnectionCompletePayload(
        tool_id=tool_id,
        provider=provider,
        connected=True,
        account_label=label or None,
        account_email=email or None,
        message=f"Connected {provider} account {email or label or 'unknown account'}.",
    )


def complete_github_connection(code: str, state: str, request_base_url: str | None = None) -> AssistantToolConnectionCompletePayload:
    _load_local_env()
    tool_id = _consume_oauth_state("github", state)
    provider = "GitHub"
    if tool_id != "github":
        return AssistantToolConnectionCompletePayload(
            tool_id="github",
            provider=provider,
            message="GitHub OAuth state is invalid or expired. Start the connection again.",
        )
    client_id, client_secret = _oauth_client_credentials("github")
    redirect_uri = _oauth_redirect_uri("github", request_base_url)
    if not client_id or not client_secret or not redirect_uri:
        return AssistantToolConnectionCompletePayload(
            tool_id=tool_id,
            provider=provider,
            message="GitHub OAuth callback is not configured. Set GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, and OMNIX_ASSISTANT_TOOLS_GITHUB_REDIRECT_URI.",
        )
    token_payload = _post_form_json(
        "https://github.com/login/oauth/access_token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
    )
    access_token = str(token_payload.get("access_token") or "")
    if not access_token:
        return AssistantToolConnectionCompletePayload(tool_id=tool_id, provider=provider, message="GitHub did not return an access token.")
    profile = _get_bearer_json("https://api.github.com/user", access_token)
    email = _safe_str(profile.get("email"))
    label = _safe_str(profile.get("login")) or _safe_str(profile.get("name")) or email
    _save_connected_account(tool_id, label, email)
    _save_connected_credential(tool_id, provider, token_payload, label, email)
    return AssistantToolConnectionCompletePayload(
        tool_id=tool_id,
        provider=provider,
        connected=True,
        account_label=label or None,
        account_email=email or None,
        message=f"Connected {provider} account {label or email or 'unknown account'}.",
    )


def disconnect_tool_account(tool_id: str) -> AssistantToolDisconnectPayload:
    """Forget a connected account: revoke its grant at the provider and delete the stored token (ASVS 3.5.1).

    Revocation is best effort; the stored token is deleted either way. Google
    grants are shared by the Google tools of one account, so the grant is
    revoked only when no other Google tool still holds a token.
    """
    credential = credential_for_tool(tool_id)
    revoked = False
    if credential is not None:
        shared = tool_id in GOOGLE_TOOL_IDS and any(
            credential_for_tool(other) is not None for other in GOOGLE_TOOL_IDS - {tool_id}
        )
        if not shared:
            revoked = _revoke_at_provider(tool_id, credential)
        delete_tool_credential(tool_id)
    payload = load_assistant_tools_config()
    payload.tools = [
        tool.model_copy(update={"connection_status": "not_configured", "account_label": None,
                                "account_email": None, "connected_at": None})
        if tool.tool_id == tool_id
        else tool
        for tool in payload.tools
    ]
    save_assistant_tools_config(payload)
    if credential is None:
        message = "No stored account token to remove."
    elif revoked:
        message = "Account disconnected and its access revoked at the provider."
    else:
        message = "Account disconnected; its stored token is deleted. Revoke Omnix's access in the provider's account settings to end the grant there."
    return AssistantToolDisconnectPayload(tool_id=tool_id, revoked_at_provider=revoked, message=message)


def _revoke_at_provider(tool_id: str, credential: AssistantToolCredentialRecord) -> bool:
    token = credential.refresh_token or credential.access_token
    if not token:
        return False
    try:
        if tool_id in GOOGLE_TOOL_IDS:
            _post_form_json_status("https://oauth2.googleapis.com/revoke", {"token": token})
            return True
        if tool_id == "github":
            client_id, client_secret = _oauth_client_credentials("github")
            if not client_id or not client_secret:
                return False
            import base64

            basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            request = Request(
                f"https://api.github.com/applications/{client_id}/grant",
                data=json.dumps({"access_token": credential.access_token}).encode("utf-8"),
                headers={"Authorization": f"Basic {basic}", "Accept": "application/vnd.github+json",
                         "Content-Type": "application/json"},
                method="DELETE",
            )
            with urlopen(request, timeout=15):
                return True
    except (OSError, ValueError):
        return False
    return False


def _post_form_json_status(url: str, values: dict[str, str]) -> None:
    request = Request(url, data=urlencode(values).encode("utf-8"),
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(request, timeout=15):
        return None


def _save_connected_account(tool_id: str, account_label: str, account_email: str) -> None:
    payload = load_assistant_tools_config()
    connected_at = datetime.now(timezone.utc).isoformat()
    payload.tools = [
        tool.model_copy(
            update={
                "enabled": True,
                "connection_status": "connected",
                "account_label": account_label or None,
                "account_email": account_email or None,
                "connected_at": connected_at,
            },
        )
        if tool.tool_id == tool_id
        else tool
        for tool in payload.tools
    ]
    save_assistant_tools_config(payload)


def _save_connected_credential(tool_id: str, provider: str, token_payload: dict[str, object], account_label: str, account_email: str) -> None:
    access_token = _safe_str(token_payload.get("access_token"))
    if not access_token:
        return
    existing = credential_for_tool(tool_id)
    refresh_token = _safe_str(token_payload.get("refresh_token")) or (existing.refresh_token if existing else None)
    scopes = _safe_str(token_payload.get("scope")).split()
    upsert_tool_credential(
        AssistantToolCredentialRecord(
            tool_id=tool_id,
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=_safe_str(token_payload.get("token_type")) or "Bearer",
            scopes=scopes,
            expires_at=expires_at_from_now(token_payload.get("expires_in")),
            account_label=account_label or None,
            account_email=account_email or None,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    )


def _post_form_json(url: str, values: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, object]:
    body = urlencode(values).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_bearer_json(url: str, access_token: str) -> dict[str, object]:
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"}, method="GET")
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _safe_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _oauth_redirect_uri(provider: str, request_base_url: str | None = None) -> str:
    provider_key = provider.upper()
    configured = environment().get(f"OMNIX_ASSISTANT_TOOLS_{provider_key}_REDIRECT_URI", "").strip()
    if configured:
        return configured
    base_url = (request_base_url or "http://127.0.0.1:8000").rstrip("/")
    return f"{base_url}/api/assistant/tools/connect/{provider}/callback"


def _oauth_client_credentials(provider: str) -> tuple[str, str]:
    provider_key = provider.upper()
    env_client_id = environment().get(f"{provider_key}_OAUTH_CLIENT_ID", "").strip()
    env_client_secret = environment().get(f"{provider_key}_OAUTH_CLIENT_SECRET", "").strip()
    if env_client_id or env_client_secret:
        return env_client_id, env_client_secret
    record = oauth_client_for_provider(provider)
    if not record:
        return "", ""
    return record.client_id.strip(), record.client_secret.strip()


def _provider_for_tool(tool_id: str) -> str:
    if tool_id in GOOGLE_TOOL_IDS:
        return "google"
    if tool_id == "github":
        return "github"
    return ""


def _issue_oauth_state(provider: str, tool_id: str) -> str:
    now = time.monotonic()
    with _PENDING_OAUTH_LOCK:
        _prune_pending_oauth_locked(now)
        while len(_PENDING_OAUTH_STATES) >= _MAX_PENDING_OAUTH_STATES:
            oldest = min(_PENDING_OAUTH_STATES, key=lambda key: _PENDING_OAUTH_STATES[key][2])
            _PENDING_OAUTH_STATES.pop(oldest, None)
        token = secrets.token_urlsafe(32)
        _PENDING_OAUTH_STATES[token] = (provider, tool_id, now + _OAUTH_STATE_TTL_SECONDS)
        return token


def clear_pending_oauth_states() -> None:
    """Invalidate pending OAuth handshakes for tests and controlled resets."""

    with _PENDING_OAUTH_LOCK:
        _PENDING_OAUTH_STATES.clear()


def _consume_oauth_state(provider: str, token: str) -> str | None:
    with _PENDING_OAUTH_LOCK:
        value = _PENDING_OAUTH_STATES.pop(token, None)
    if value is None:
        return None
    stored_provider, tool_id, expires_at = value
    if stored_provider != provider or expires_at <= time.monotonic():
        return None
    return tool_id


def _prune_pending_oauth_locked(now: float) -> None:
    for token, (_provider, _tool_id, expires_at) in list(_PENDING_OAUTH_STATES.items()):
        if expires_at <= now:
            _PENDING_OAUTH_STATES.pop(token, None)


def _load_local_env() -> None:
    if environment().get("OMNIX_ASSISTANT_TOOLS_SKIP_LOCAL_ENV") == "1":
        return
    env_path = Path(__file__).resolve().parents[4] / ".env.local"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            environment().setdefault(key, value)
