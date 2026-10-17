"""Credentials of connected assistant tools, kept in the secret store (WP-4.9).

OAuth tokens and OAuth client secrets never reach PostgreSQL, the settings
document or a plaintext file: each workspace's credentials are one secret in
``app.security.secrets``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.security.secrets import require_writable, secret_store


class AssistantToolCredentialRecord(BaseModel):
    tool_id: str
    provider: str
    access_token: str = ""
    refresh_token: str | None = None
    token_type: str = "Bearer"
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    account_label: str | None = None
    account_email: str | None = None
    updated_at: str


class AssistantToolCredentialsPayload(BaseModel):
    credentials: list[AssistantToolCredentialRecord] = Field(default_factory=list)


class AssistantToolOAuthClientRecord(BaseModel):
    provider: str
    client_id: str
    client_secret: str
    updated_at: str


class AssistantToolOAuthClientsPayload(BaseModel):
    clients: list[AssistantToolOAuthClientRecord] = Field(default_factory=list)


def _secret_name(kind: str) -> str:
    from app.runtime.tenant_context import LOCAL_WORKSPACE_ID, current_tenant

    try:
        workspace = current_tenant().workspace_id
    except RuntimeError:
        workspace = LOCAL_WORKSPACE_ID
    return f"assistant-tools/{workspace}/{kind}"


def load_assistant_tool_credentials() -> AssistantToolCredentialsPayload:
    raw = secret_store().get(_secret_name("credentials"))
    return AssistantToolCredentialsPayload.model_validate_json(raw) if raw else AssistantToolCredentialsPayload()


def save_assistant_tool_credentials(payload: AssistantToolCredentialsPayload) -> AssistantToolCredentialsPayload:
    require_writable(secret_store()).set(_secret_name("credentials"), payload.model_dump_json())
    return payload


def load_assistant_tool_oauth_clients() -> AssistantToolOAuthClientsPayload:
    raw = secret_store().get(_secret_name("oauth-clients"))
    return AssistantToolOAuthClientsPayload.model_validate_json(raw) if raw else AssistantToolOAuthClientsPayload()


def save_assistant_tool_oauth_clients(payload: AssistantToolOAuthClientsPayload) -> AssistantToolOAuthClientsPayload:
    require_writable(secret_store()).set(_secret_name("oauth-clients"), payload.model_dump_json())
    return payload


def oauth_client_for_provider(provider: str) -> AssistantToolOAuthClientRecord | None:
    normalized = provider.lower()
    return next(
        (record for record in load_assistant_tool_oauth_clients().clients if record.provider.lower() == normalized),
        None,
    )


def upsert_oauth_client(record: AssistantToolOAuthClientRecord) -> AssistantToolOAuthClientRecord:
    payload = load_assistant_tool_oauth_clients()
    normalized = record.provider.lower()
    payload.clients = [current for current in payload.clients if current.provider.lower() != normalized]
    payload.clients.append(record)
    save_assistant_tool_oauth_clients(payload)
    return record


def credential_for_tool(tool_id: str) -> AssistantToolCredentialRecord | None:
    return next((record for record in load_assistant_tool_credentials().credentials if record.tool_id == tool_id), None)


def upsert_tool_credential(record: AssistantToolCredentialRecord) -> AssistantToolCredentialRecord:
    payload = load_assistant_tool_credentials()
    payload.credentials = [current for current in payload.credentials if current.tool_id != record.tool_id]
    payload.credentials.append(record)
    save_assistant_tool_credentials(payload)
    return record


def delete_tool_credential(tool_id: str) -> None:
    payload = load_assistant_tool_credentials()
    remaining = [record for record in payload.credentials if record.tool_id != tool_id]
    if len(remaining) == len(payload.credentials):
        return
    payload.credentials = remaining
    save_assistant_tool_credentials(payload)


def expires_at_from_now(expires_in: object) -> str | None:
    try:
        seconds = int(expires_in) if isinstance(expires_in, (int, str, bytes, bytearray)) else 0
    except (TypeError, ValueError):
        seconds = 0
    if seconds <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc) + timedelta(seconds=60)
