"""Gmail runtime adapter foundation for assistant tools."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import base64
import json
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .credentials import AssistantToolCredentialRecord, credential_for_tool, expires_at_from_now, is_expired, upsert_tool_credential
from .models import AssistantToolRequest, AssistantToolResult


@dataclass(frozen=True)
class GmailMessageRecord:
    id: str
    sender: str
    subject: str
    snippet: str
    thread_id: str | None = None


@dataclass(frozen=True)
class GmailDraftRecord:
    id: str
    to: str
    subject: str
    body: str


@dataclass(frozen=True)
class GmailSentRecord:
    id: str
    to: str
    subject: str
    thread_id: str | None = None


MAX_RECIPIENTS = 20


def _validated_recipients(to: str) -> str:
    """Comma-separated addresses, each with a local part and a domain; at most 20."""
    from email.utils import getaddresses

    addresses = [address for _name, address in getaddresses([to]) if address]
    if not addresses or len(addresses) > MAX_RECIPIENTS:
        raise ValueError("gmail_send_invalid_recipients")
    for address in addresses:
        local, _, domain = address.rpartition("@")
        if not local or "." not in domain or any(char.isspace() for char in address):
            raise ValueError("gmail_send_invalid_recipients")
    return ", ".join(addresses)


class GmailRuntimeAdapter(Protocol):
    def search_messages(self, query: str, limit: int = 10) -> list[GmailMessageRecord]: ...

    def create_draft(self, *, to: str, subject: str, body: str) -> GmailDraftRecord: ...

    def update_draft(self, *, draft_id: str, to: str | None = None, subject: str | None = None, body: str | None = None) -> GmailDraftRecord: ...

    def send_message(self, *, to: str, subject: str, body: str, draft_id: str | None = None) -> GmailSentRecord: ...


@dataclass
class FakeGmailRuntimeAdapter:
    messages: list[GmailMessageRecord] = field(default_factory=list)
    drafts: dict[str, GmailDraftRecord] = field(default_factory=dict)
    sent: list[GmailSentRecord] = field(default_factory=list)

    def send_message(self, *, to: str, subject: str, body: str, draft_id: str | None = None) -> GmailSentRecord:
        if draft_id is not None:
            draft = self.drafts.pop(draft_id)
            to, subject = draft.to, draft.subject
        record = GmailSentRecord(id=f"msg-{uuid.uuid4().hex[:12]}", to=to, subject=subject)
        self.sent.append(record)
        return record

    def search_messages(self, query: str, limit: int = 10) -> list[GmailMessageRecord]:
        needle = query.lower().strip()
        if not needle:
            return self.messages[:limit]
        matches = [
            message
            for message in self.messages
            if needle in message.sender.lower() or needle in message.subject.lower() or needle in message.snippet.lower()
        ]
        return matches[:limit]

    def create_draft(self, *, to: str, subject: str, body: str) -> GmailDraftRecord:
        draft = GmailDraftRecord(id=f"draft-{uuid.uuid4().hex[:12]}", to=to, subject=subject, body=body)
        self.drafts[draft.id] = draft
        return draft

    def update_draft(self, *, draft_id: str, to: str | None = None, subject: str | None = None, body: str | None = None) -> GmailDraftRecord:
        current = self.drafts.get(draft_id)
        if current is None:
            raise KeyError("draft_not_found")
        draft = GmailDraftRecord(
            id=current.id,
            to=to if to is not None else current.to,
            subject=subject if subject is not None else current.subject,
            body=body if body is not None else current.body,
        )
        self.drafts[draft.id] = draft
        return draft


@dataclass
class GoogleGmailRuntimeAdapter:
    credential: AssistantToolCredentialRecord

    def search_messages(self, query: str, limit: int = 10) -> list[GmailMessageRecord]:
        token = self._access_token()
        list_payload = _gmail_json(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{urlencode({'q': query, 'maxResults': str(limit)})}",
            token,
        )
        messages: list[GmailMessageRecord] = []
        for row in list_payload.get("messages", []) if isinstance(list_payload.get("messages"), list) else []:
            message_id = str(row.get("id") or "") if isinstance(row, dict) else ""
            if not message_id:
                continue
            detail = _gmail_json(
                "GET",
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?{urlencode({'format': 'metadata', 'metadataHeaders': ['From', 'Subject']}, doseq=True)}",
                token,
            )
            messages.append(_gmail_message_from_payload(detail))
        return messages

    def create_draft(self, *, to: str, subject: str, body: str) -> GmailDraftRecord:
        token = self._access_token()
        raw = _base64url_message(to=to, subject=subject, body=body)
        payload = _gmail_json(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            token,
            body={"message": {"raw": raw}},
        )
        draft_id = str(payload.get("id") or "")
        return GmailDraftRecord(id=draft_id or f"draft-{uuid.uuid4().hex[:12]}", to=to, subject=subject, body=body)

    def update_draft(self, *, draft_id: str, to: str | None = None, subject: str | None = None, body: str | None = None) -> GmailDraftRecord:
        raise NotImplementedError("gmail_update_draft_not_available")

    def send_message(self, *, to: str, subject: str, body: str, draft_id: str | None = None) -> GmailSentRecord:
        """Send a new message, or an existing draft by id (``drafts.send``)."""
        token = self._access_token()
        if draft_id:
            payload = _gmail_json(
                "POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send", token, body={"id": draft_id},
            )
        else:
            payload = _gmail_json(
                "POST",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                token,
                body={"raw": _base64url_message(to=to, subject=subject, body=body)},
            )
        return GmailSentRecord(
            id=str(payload.get("id") or ""), to=to, subject=subject,
            thread_id=str(payload.get("threadId") or "") or None,
        )

    def _access_token(self) -> str:
        if not is_expired(self.credential.expires_at):
            return self.credential.access_token
        refreshed = _refresh_google_credential(self.credential)
        self.credential = refreshed
        return refreshed.access_token


def default_fake_gmail_adapter() -> FakeGmailRuntimeAdapter:
    return FakeGmailRuntimeAdapter(
        messages=[
            GmailMessageRecord(id="msg-1", sender="ada@example.com", subject="Receipt", snippet="Your order receipt is attached.", thread_id="thread-1"),
            GmailMessageRecord(id="msg-2", sender="team@example.com", subject="Project update", snippet="CI is green and the PR is ready.", thread_id="thread-2"),
        ]
    )


_DEFAULT_GMAIL_ADAPTER = default_fake_gmail_adapter()


def get_gmail_runtime_adapter() -> GmailRuntimeAdapter | None:
    """The connected Gmail account; ``None`` when no Google account is connected.

    Sample messages are used only when ``OMNIX_ASSISTANT_TOOLS_FAKE_GMAIL`` is
    set (demos and tests), never as a silent fallback for a real request.
    """
    if _env_str("OMNIX_ASSISTANT_TOOLS_FAKE_GMAIL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _DEFAULT_GMAIL_ADAPTER
    credential = credential_for_tool("gmail")
    if credential and credential.access_token:
        return GoogleGmailRuntimeAdapter(credential=credential)
    return None


def run_gmail_tool_request(request: AssistantToolRequest, adapter: GmailRuntimeAdapter | None = None) -> AssistantToolResult:
    runtime = adapter or get_gmail_runtime_adapter()
    if runtime is None:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level="low",
            state_changed=False,
            result_summary="Gmail is not connected. Connect a Google account to use Gmail.",
            output={"connection_status": "missing_credentials"},
            error="missing_credentials",
        )
    try:
        if request.action_id == "gmail.read_email":
            query = str(request.input.get("query") or request.input.get("q") or "")
            limit = int(request.input.get("limit") or 10)
            messages = runtime.search_messages(query, limit=limit)
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="low",
                state_changed=False,
                result_summary=f"Found {len(messages)} Gmail message{'s' if len(messages) != 1 else ''}.",
                output={"messages": [message.__dict__ for message in messages]},
            )
        if request.action_id == "gmail.create_draft":
            draft = runtime.create_draft(
                to=str(request.input.get("to") or ""),
                subject=str(request.input.get("subject") or ""),
                body=str(request.input.get("body") or ""),
            )
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="medium",
                state_changed=True,
                result_summary=f"Created Gmail draft {draft.id} to {draft.to}.",
                output={"draft": draft.__dict__},
            )
        if request.action_id == "gmail.send_email":
            # Reached only through an approved proposal: the capability is
            # high risk and requires confirmation (capabilities.registry).
            draft_id = str(request.input.get("draft_id") or "").strip() or None
            to = "" if draft_id else _validated_recipients(str(request.input.get("to") or ""))
            sent = runtime.send_message(
                to=to,
                subject=str(request.input.get("subject") or ""),
                body=str(request.input.get("body") or ""),
                draft_id=draft_id,
            )
            target = f"draft {draft_id}" if draft_id else sent.to
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="high",
                state_changed=True,
                result_summary=f"Sent Gmail message {sent.id} ({target}).",
                output={"sent": sent.__dict__},
            )
    except Exception as error:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            risk_level="medium",
            state_changed=False,
            result_summary="Gmail action failed while using the connected account.",
            error=str(error) or "gmail_runtime_error",
        )
    return AssistantToolResult(
        tool_id=request.tool_id,
        action_id=request.action_id,
        session_id=request.session_id,
        risk_level="high",
        state_changed=False,
        result_summary="Gmail action is approval-gated or not connected to a runtime adapter yet.",
        error="gmail_action_not_available",
    )


def _gmail_json(method: str, url: str, access_token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _gmail_message_from_payload(payload: dict[str, Any]) -> GmailMessageRecord:
    headers = {
        str(header.get("name") or "").lower(): str(header.get("value") or "")
        for header in (payload.get("payload", {}).get("headers", []) if isinstance(payload.get("payload"), dict) else [])
        if isinstance(header, dict)
    }
    return GmailMessageRecord(
        id=str(payload.get("id") or ""),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        snippet=str(payload.get("snippet") or ""),
        thread_id=str(payload.get("threadId") or "") or None,
    )


def _base64url_message(*, to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


def _refresh_google_credential(credential: AssistantToolCredentialRecord) -> AssistantToolCredentialRecord:
    if not credential.refresh_token:
        return credential
    client_id = _env_str("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = _env_str("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return credential
    payload = _post_form_json(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token,
        },
    )
    access_token = str(payload.get("access_token") or "")
    if not access_token:
        return credential
    refreshed = credential.model_copy(
        update={
            "access_token": access_token,
            "expires_at": expires_at_from_now(payload.get("expires_in")),
        }
    )
    upsert_tool_credential(refreshed)
    return refreshed


def _post_form_json(url: str, values: dict[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(values).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
