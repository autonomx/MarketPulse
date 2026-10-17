from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import html
import re
from datetime import datetime, time, timezone
from typing import Any

from ..contracts import IssuerIdentity, TradingEvidence, fingerprint
from .base import AdapterExecutionResult

_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-1/A", "S-3", "S-3/A", "424B3", "424B5", "RW", "EFFECT"}


def _parse_time(value: Any, fallback_date: str | None = None) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    if fallback_date:
        try:
            d = datetime.fromisoformat(fallback_date).date()
            return datetime.combine(d, time(0, 0), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _plain_text(raw: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())[:250_000]


class SecEdgarAdapter:
    name = "sec_edgar"

    def __init__(self, runtime=None) -> None:
        if runtime is None:
            from app.apps.trading.providers.http_runtime import ProviderHttpRuntime
            runtime = ProviderHttpRuntime("sec_edgar_research", max_concurrency=1)
        self.runtime = runtime

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": _env_str(
                "OMNIX_SEC_USER_AGENT",
                "OmnixTradingResearch/1.0 local-research contact=local@localhost",
            ),
            "Accept-Encoding": "gzip, deflate",
        }

    def find(self, identity: IssuerIdentity, *, query: str | None = None, limit: int = 20) -> AdapterExecutionResult:
        if not identity.cik:
            return AdapterExecutionResult(detail="issuer_cik_unresolved", warnings=("issuer_cik_unresolved",))
        response = self.runtime.get(
            f"https://data.sec.gov/submissions/CIK{identity.cik}.json",
            headers=self._headers(), timeout=20,
        )
        payload = response.json()
        recent = ((payload.get("filings") or {}).get("recent") or {}) if isinstance(payload, dict) else {}
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        documents = recent.get("primaryDocument") or []
        filing_dates = recent.get("filingDate") or []
        acceptances = recent.get("acceptanceDateTime") or []
        captured = datetime.now(timezone.utc)
        selected_forms = {token.strip().upper() for token in (query or "").split(",") if token.strip()} or _FORMS
        evidence: list[TradingEvidence] = []
        cik_numeric = str(int(identity.cik))
        for index, form in enumerate(forms):
            form = str(form).upper()
            if form not in selected_forms or len(evidence) >= limit:
                continue
            accession = str(accessions[index]) if index < len(accessions) else ""
            document = str(documents[index]) if index < len(documents) else ""
            if not accession or not document:
                continue
            compact_accession = accession.replace("-", "")
            locator = f"https://www.sec.gov/Archives/edgar/data/{cik_numeric}/{compact_accession}/{document}"
            filing_date = str(filing_dates[index]) if index < len(filing_dates) else None
            accepted = _parse_time(acceptances[index] if index < len(acceptances) else None, filing_date)
            content = f"SEC filing {form}; accession {accession}; primary document {document}."
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            meta = {"form": form, "accession": accession, "primary_document": document}
            fp = fingerprint({"instrument_id": identity.instrument_id, "locator": locator, "content_hash": content_hash})
            evidence.append(TradingEvidence(
                evidence_id=f"sec-{hashlib.sha256(locator.encode()).hexdigest()[:24]}",
                instrument_id=identity.instrument_id,
                issuer_identity_id=identity.identity_id,
                evidence_type="sec_filing_metadata",
                source_type="sec",
                source_locator=locator,
                source_authority_tier=1,
                source_published_at=accepted,
                source_available_at=accepted,
                captured_at=captured,
                title=f"{form} {accession}",
                content=content,
                content_hash=content_hash,
                extraction_status="metadata",
                metadata=meta,
                immutable_fingerprint=fp,
            ))
        return AdapterExecutionResult(evidence=tuple(evidence), detail=f"sec_filings:{len(evidence)}")

    def extract(self, identity: IssuerIdentity, *, locator: str) -> AdapterExecutionResult:
        if "sec.gov/Archives/" not in locator:
            raise ValueError("sec_extract_locator_not_sec_archive")
        response = self.runtime.get(locator, headers=self._headers(), timeout=30)
        content = _plain_text(response.text)
        if not content:
            raise ValueError("sec_filing_extraction_empty")
        captured = datetime.now(timezone.utc)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        fp = fingerprint({"instrument_id": identity.instrument_id, "locator": locator, "content_hash": content_hash})
        evidence = TradingEvidence(
            evidence_id=f"secx-{hashlib.sha256((locator + content_hash).encode()).hexdigest()[:24]}",
            instrument_id=identity.instrument_id,
            issuer_identity_id=identity.identity_id,
            evidence_type="sec_filing_content",
            source_type="sec",
            source_locator=locator,
            source_authority_tier=1,
            captured_at=captured,
            title="SEC filing extracted content",
            content=content,
            content_hash=content_hash,
            extraction_status="completed",
            metadata={},
            immutable_fingerprint=fp,
        )
        return AdapterExecutionResult(evidence=(evidence,), detail="sec_filing_extracted")
