from __future__ import annotations

from urllib.parse import urlparse

from ..contracts import IssuerIdentity, TradingEvidence
from .base import AdapterExecutionResult
from .generic_web import GenericWebAdapter

_SECONDARY_HOSTS = {"reuters.com", "finance.yahoo.com", "bloomberg.com", "marketwatch.com", "wsj.com"}


def _looks_like_ir(locator: str) -> bool:
    parsed = urlparse(locator)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if any(host == item or host.endswith("." + item) for item in _SECONDARY_HOSTS):
        return False
    path = (parsed.path or "").lower()
    return any(token in path or token in host for token in ("investor", "ir.", "newsroom", "press-release", "press_releases"))


class CompanyIrAdapter:
    name = "company_ir"

    def __init__(self, web: GenericWebAdapter | None = None) -> None:
        self.web = web or GenericWebAdapter()

    def find(self, identity: IssuerIdentity, *, query: str | None = None, limit: int = 8) -> AdapterExecutionResult:
        query = query or f"{identity.legal_name or identity.symbol} {identity.symbol} investor relations press release"
        result = self.web.find(identity, query=query, limit=limit)
        values: list[TradingEvidence] = []
        for item in result.evidence:
            if not _looks_like_ir(item.source_locator):
                continue
            values.append(item.model_copy(update={"source_type": "company_ir", "source_authority_tier": 1, "evidence_type": "company_ir_result"}))
        return AdapterExecutionResult(evidence=tuple(values), detail=f"company_ir_results:{len(values)}", warnings=result.warnings)

    def extract(self, identity: IssuerIdentity, *, locator: str) -> AdapterExecutionResult:
        result = self.web.extract(identity, locator=locator)
        if not _looks_like_ir(locator):
            return result
        return AdapterExecutionResult(
            evidence=tuple(item.model_copy(update={"source_type": "company_ir", "source_authority_tier": 1, "evidence_type": "company_ir_release"}) for item in result.evidence),
            detail="company_ir_release_extracted",
        )
