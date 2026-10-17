from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
from datetime import datetime, timezone
from typing import Any, cast

from httpx import HTTPError

from app.apps.trading.providers.errors import ProviderContractError, ProviderUnavailableError

from .contracts import IssuerIdentity, fingerprint

_SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"


def _symbol_exchange(instrument_id: str) -> tuple[str, str | None]:
    parts = [part for part in instrument_id.split(":") if part]
    if not parts:
        raise ValueError("invalid_instrument_id")
    symbol = parts[-1].upper()
    exchange = parts[-2].upper() if len(parts) >= 3 else None
    return symbol, exchange


class SecIssuerIdentityResolver:
    def __init__(self, runtime=None) -> None:
        if runtime is None:
            from app.apps.trading.providers.http_runtime import ProviderHttpRuntime
            runtime = ProviderHttpRuntime("sec_issuer_identity", max_concurrency=1)
        self.runtime = runtime
        self._mapping: dict[str, dict[str, Any]] | None = None

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "User-Agent": _env_str(
                "OMNIX_SEC_USER_AGENT",
                "OmnixTradingResearch/1.0 local-research contact=local@localhost",
            ),
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov",
        }

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._mapping is not None:
            return self._mapping
        try:
            response = self.runtime.get(_SEC_TICKERS, headers=self._headers(), timeout=20)
        except HTTPError as exc:
            raise ProviderUnavailableError(f"SEC issuer directory unavailable: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("SEC issuer directory returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderContractError("sec_company_tickers_malformed")
        mapping: dict[str, dict[str, Any]] = {}
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            if ticker:
                mapping[ticker] = row
        self._mapping = mapping
        return mapping

    def resolve(self, instrument_id: str) -> IssuerIdentity:
        symbol, exchange = _symbol_exchange(instrument_id)
        row = self._load().get(symbol)
        captured = datetime.now(timezone.utc)
        cik = None if row is None else str(row.get("cik_str") or "").zfill(10)
        legal_name = None if row is None else str(row.get("title") or "").strip() or None
        confidence = "1" if row is not None else "0"
        payload = {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "exchange": exchange,
            "legal_name": legal_name,
            "cik": cik,
            "source": "sec_company_tickers",
            "confidence": confidence,
        }
        fp = fingerprint(payload)
        return IssuerIdentity(
            identity_id=f"issuer-{hashlib.sha256((instrument_id + '|' + str(cik)).encode()).hexdigest()[:24]}",
            instrument_id=instrument_id,
            symbol=symbol,
            exchange=exchange,
            legal_name=legal_name,
            cik=cik,
            source="sec_company_tickers",
            source_available_at=captured,
            captured_at=captured,
            confidence=cast(Any, confidence),
            immutable_fingerprint=fp,
        )


def fallback_issuer_identity(instrument_id: str) -> IssuerIdentity:
    """Build a low-confidence identity when the SEC directory is unavailable.

    Research can still use symbol-based web/company queries without a CIK. The
    fallback is deliberately marked as unresolved so downstream coverage stays
    partial instead of presenting it as authoritative issuer identity data.
    """

    symbol, exchange = _symbol_exchange(instrument_id)
    captured = datetime.now(timezone.utc)
    payload = {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "exchange": exchange,
        "source": "instrument_id_fallback",
    }
    return IssuerIdentity(
        identity_id=f"issuer-fallback-{hashlib.sha256(instrument_id.encode()).hexdigest()[:24]}",
        instrument_id=instrument_id,
        symbol=symbol,
        exchange=exchange,
        source="instrument_id_fallback",
        source_available_at=None,
        captured_at=captured,
        confidence=cast(Any, "0"),
        immutable_fingerprint=fingerprint(payload),
    )
