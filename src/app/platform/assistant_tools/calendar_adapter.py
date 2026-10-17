"""Approval-gated Google Calendar runtime adapter for assistant tools."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connections import AssistantToolConnectionError, google_access_token_for_tool
from .models import AssistantToolRequest, AssistantToolResult


@dataclass(frozen=True)
class CalendarEventRecord:
    id: str
    title: str
    start_time: str
    end_time: str
    attendees: tuple[str, ...] = ()
    location: str = ""
    html_link: str = ""


class CalendarRuntimeAdapter(Protocol):
    def read_availability(self, *, start_time: str, end_time: str, timezone_name: str = "") -> list[CalendarEventRecord]: ...

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        attendees: tuple[str, ...],
        location: str = "",
        description: str = "",
        timezone_name: str = "",
        reminder_minutes: int | None = None,
    ) -> CalendarEventRecord: ...


@dataclass
class FakeCalendarRuntimeAdapter:
    events: list[CalendarEventRecord] = field(default_factory=list)

    def read_availability(self, *, start_time: str, end_time: str, timezone_name: str = "") -> list[CalendarEventRecord]:
        return [event for event in self.events if event.start_time < end_time and event.end_time > start_time]

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        attendees: tuple[str, ...],
        location: str = "",
        description: str = "",
        timezone_name: str = "",
        reminder_minutes: int | None = None,
    ) -> CalendarEventRecord:
        event = CalendarEventRecord(
            id=f"event-{uuid.uuid4().hex[:12]}",
            title=title,
            start_time=start_time,
            end_time=end_time,
            attendees=attendees,
            location=location,
        )
        self.events.append(event)
        return event


class CalendarAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleCalendarRuntimeAdapter:
    calendar_id: str = "primary"
    timeout_seconds: float = 15.0

    def read_availability(self, *, start_time: str, end_time: str, timezone_name: str = "") -> list[CalendarEventRecord]:
        start, end = _validate_time_range(start_time, end_time, timezone_name=timezone_name)
        query = urlencode(
            {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "50",
            }
        )
        payload = self._request("GET", f"/calendars/{self.calendar_id}/events?{query}")
        return [_event_record(item) for item in payload.get("items", []) if isinstance(item, dict)]

    def create_event(
        self,
        *,
        title: str,
        start_time: str,
        end_time: str,
        attendees: tuple[str, ...],
        location: str = "",
        description: str = "",
        timezone_name: str = "",
        reminder_minutes: int | None = None,
    ) -> CalendarEventRecord:
        _validate_time_range(start_time, end_time, timezone_name=timezone_name)
        if reminder_minutes is not None and not 0 <= reminder_minutes <= 40_320:
            raise CalendarAdapterError("Reminder minutes must be between 0 and 40320.")
        start: dict[str, object] = {"dateTime": start_time}
        end: dict[str, object] = {"dateTime": end_time}
        if timezone_name:
            start["timeZone"] = timezone_name
            end["timeZone"] = timezone_name
        body: dict[str, object] = {
            "summary": title.strip() or "Untitled event",
            "description": description.strip(),
            "location": location.strip(),
            "start": start,
            "end": end,
            "attendees": [{"email": email} for email in attendees],
            "reminders": (
                {"useDefault": True}
                if reminder_minutes is None
                else {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": reminder_minutes}],
                }
            ),
        }
        payload = self._request("POST", f"/calendars/{self.calendar_id}/events?sendUpdates=none", body)
        return _event_record(payload)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, Any]:
        try:
            token = google_access_token_for_tool("calendar")
            request = Request(
                f"https://www.googleapis.com/calendar/v3{path}",
                data=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method=method,
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except AssistantToolConnectionError:
            raise
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
            raise CalendarAdapterError(f"Google Calendar request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise CalendarAdapterError("Google Calendar returned an invalid response.")
        return payload


def default_fake_calendar_adapter() -> FakeCalendarRuntimeAdapter:
    return FakeCalendarRuntimeAdapter(
        events=[
            CalendarEventRecord(
                id="event-1",
                title="Focus block",
                start_time="2026-07-03T09:00:00",
                end_time="2026-07-03T10:00:00",
            )
        ]
    )


_DEFAULT_CALENDAR_ADAPTER = default_fake_calendar_adapter()


def get_calendar_runtime_adapter() -> CalendarRuntimeAdapter:
    if _env_str("OMNIX_ASSISTANT_TOOLS_FAKE_CALENDAR", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _DEFAULT_CALENDAR_ADAPTER
    return GoogleCalendarRuntimeAdapter()


def _as_attendees(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def run_calendar_tool_request(request: AssistantToolRequest, adapter: CalendarRuntimeAdapter | None = None) -> AssistantToolResult:
    runtime = adapter or get_calendar_runtime_adapter()
    try:
        if request.action_id == "calendar.read_availability":
            start_time = str(request.input.get("start_time") or request.input.get("start") or "")
            end_time = str(request.input.get("end_time") or request.input.get("end") or "")
            events = runtime.read_availability(
                start_time=start_time,
                end_time=end_time,
                timezone_name=str(request.input.get("timezone") or ""),
            )
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="low",
                state_changed=False,
                result_summary=f"Found {len(events)} calendar conflict{'s' if len(events) != 1 else ''}.",
                output={"events": [event.__dict__ for event in events]},
            )
        if request.action_id == "calendar.create_event":
            reminder_value = request.input.get("reminder_minutes")
            reminder_minutes = int(reminder_value) if reminder_value not in {None, ""} else None
            event = runtime.create_event(
                title=str(request.input.get("title") or "Untitled event"),
                start_time=str(request.input.get("start_time") or request.input.get("start") or ""),
                end_time=str(request.input.get("end_time") or request.input.get("end") or ""),
                attendees=_as_attendees(request.input.get("attendees") or []),
                location=str(request.input.get("location") or ""),
                description=str(request.input.get("description") or ""),
                timezone_name=str(request.input.get("timezone") or ""),
                reminder_minutes=reminder_minutes,
            )
            return AssistantToolResult(
                tool_id=request.tool_id,
                action_id=request.action_id,
                session_id=request.session_id,
                risk_level="medium",
                state_changed=True,
                result_summary=f"Created calendar event {event.id}: {event.title}.",
                output={"event": event.__dict__},
            )
    except (AssistantToolConnectionError, CalendarAdapterError, TypeError, ValueError) as exc:
        return AssistantToolResult(
            tool_id=request.tool_id,
            action_id=request.action_id,
            session_id=request.session_id,
            error=str(exc),
            result_summary="Google Calendar action failed safely.",
        )
    return AssistantToolResult(
        tool_id=request.tool_id,
        action_id=request.action_id,
        session_id=request.session_id,
        error="calendar_action_not_available",
    )


def _validate_time_range(start_time: str, end_time: str, *, timezone_name: str = "") -> tuple[datetime, datetime]:
    if not start_time or not end_time:
        raise CalendarAdapterError("Calendar start and end times are required.")
    try:
        start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarAdapterError("Calendar times must use ISO 8601 format.") from exc
    if start.tzinfo is None or end.tzinfo is None:
        if not timezone_name:
            raise CalendarAdapterError("Calendar times need a timezone offset or timezone name.")
        try:
            timezone_info = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise CalendarAdapterError("Calendar timezone must be a valid IANA timezone.") from exc
        start = start.replace(tzinfo=timezone_info)
        end = end.replace(tzinfo=timezone_info)
    if end <= start:
        raise CalendarAdapterError("Calendar end time must be after the start time.")
    return start, end


def _event_record(payload: dict[str, Any]) -> CalendarEventRecord:
    start = payload.get("start") if isinstance(payload.get("start"), dict) else {}
    end = payload.get("end") if isinstance(payload.get("end"), dict) else {}
    attendees = payload.get("attendees") if isinstance(payload.get("attendees"), list) else []
    return CalendarEventRecord(
        id=str(payload.get("id") or ""),
        title=str(payload.get("summary") or "Untitled event"),
        start_time=str(start.get("dateTime") or start.get("date") or ""),
        end_time=str(end.get("dateTime") or end.get("date") or ""),
        attendees=tuple(str(item.get("email") or "") for item in attendees if isinstance(item, dict) and item.get("email")),
        location=str(payload.get("location") or ""),
        html_link=str(payload.get("htmlLink") or ""),
    )
