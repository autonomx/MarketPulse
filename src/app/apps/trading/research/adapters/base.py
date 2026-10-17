from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts import IssuerIdentity, TradingEvidence


@dataclass(frozen=True)
class AdapterExecutionResult:
    evidence: tuple[TradingEvidence, ...] = ()
    detail: str | None = None
    warnings: tuple[str, ...] = ()


class TradingResearchAdapter(Protocol):
    name: str

    def find(self, identity: IssuerIdentity, *, query: str | None = None, limit: int = 10) -> AdapterExecutionResult: ...
    def extract(self, identity: IssuerIdentity, *, locator: str) -> AdapterExecutionResult: ...
