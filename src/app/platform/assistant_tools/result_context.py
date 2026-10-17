"""Assistant-readable context derived from governed tool results."""
from __future__ import annotations

from pydantic import BaseModel

from .ledger import AssistantToolLedgerEntry
from .models import AssistantToolResult


class AssistantToolResultContext(BaseModel):
    message: str
    ledger_ref: str | None = None
    state_changed: bool = False
    sensitive_summary: str = ""


def tool_result_to_chat_context(result: AssistantToolResult, ledger_entry: AssistantToolLedgerEntry | None = None) -> AssistantToolResultContext:
    if result.error:
        message = f"Tool action {result.action_id} did not run: {result.error}."
    elif result.result_summary:
        message = result.result_summary
    else:
        message = f"Tool action {result.action_id} completed."
    ledger_ref = ledger_entry.execution_id if ledger_entry else None
    if ledger_ref:
        message = f"{message} Ledger: {ledger_ref}."
    return AssistantToolResultContext(
        message=message,
        ledger_ref=ledger_ref,
        state_changed=result.state_changed,
        sensitive_summary="Raw tool output is summarized; expand the ledger entry for details.",
    )


def tool_result_message(result: AssistantToolResult, ledger_entry: AssistantToolLedgerEntry | None = None) -> str:
    return tool_result_to_chat_context(result, ledger_entry).message
