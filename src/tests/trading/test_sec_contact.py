"""SEC requests carry the signed-in user's email as their contact (MarketPulse W-5)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import sec_contact
from app.apps.trading.company_profiles import CompanyProfiles, SecCompanySource
from app.apps.trading.corporate_events import CorporateEvent, CorporateEventsService
from app.apps.trading.fundamentals import FinancialsService, create_trading_fundamentals_router
from app.apps.trading.sec_contact import SecContact, SecContactRequired
from app.runtime.tenant_context import LOCAL_USER_ID

NOW = datetime(2026, 10, 17, 14, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_operator_agent(monkeypatch):
    monkeypatch.delenv("OMNIX_SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("OMNIX_PRODUCT_NAME", "MarketPulse")


def contact(user: str, emails: dict[str, str | None], lookups: list[str] | None = None, clock=lambda: 0.0) -> SecContact:
    def email_of(user_id: str) -> str | None:
        if lookups is not None:
            lookups.append(user_id)
        return emails.get(user_id)

    return SecContact(user=lambda: user, email_of=email_of, clock=clock)


def test_the_signed_in_users_email_is_the_contact() -> None:
    agent = contact("user:ana", {"user:ana": "ana@example.com", LOCAL_USER_ID: "owner@example.com"}).user_agent()
    assert agent == "MarketPulse ana@example.com"


def test_a_user_without_an_email_falls_back_to_the_install_owner() -> None:
    assert contact("user:guest", {LOCAL_USER_ID: "owner@example.com"}).user_agent() == "MarketPulse owner@example.com"


def test_without_any_email_no_request_is_sent_and_the_user_is_asked_for_one() -> None:
    with pytest.raises(SecContactRequired, match="Add an email to your account"):
        contact(LOCAL_USER_ID, {}).user_agent()


def test_an_operators_user_agent_wins(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_SEC_USER_AGENT", "Research desk desk@example.com")
    lookups: list[str] = []
    assert contact("user:ana", {"user:ana": "ana@example.com"}, lookups).user_agent() == "Research desk desk@example.com"
    assert lookups == []


def test_each_users_email_is_looked_up_at_most_once_a_minute() -> None:
    now = [0.0]
    lookups: list[str] = []
    resolver = contact("user:ana", {"user:ana": "ana@example.com"}, lookups, clock=lambda: now[0])
    resolver.user_agent()
    now[0] = 59.0
    resolver.user_agent()
    assert lookups == ["user:ana"]
    now[0] = 61.0
    resolver.user_agent()
    assert lookups == ["user:ana", "user:ana"]


def test_the_requesting_user_is_the_requests_principal(monkeypatch) -> None:
    from app.security import request_tenant

    token = request_tenant._CURRENT_PRINCIPAL.set(SimpleNamespace(user_id="user:ana"))
    try:
        assert sec_contact._requesting_user() == "user:ana"
    finally:
        request_tenant._CURRENT_PRINCIPAL.reset(token)


def test_every_sec_request_carries_the_contact(monkeypatch) -> None:
    monkeypatch.setattr(sec_contact, "_DEFAULT", contact("user:ana", {"user:ana": "ana@example.com"}))
    seen: list[dict[str, str]] = []

    class Runtime:
        def get(self, url, *, headers, timeout):
            seen.append(headers)
            return SimpleNamespace(json=lambda: {})

    SecCompanySource(runtime=Runtime()).tickers()
    from app.apps.trading.research.adapters.sec_edgar import SecEdgarAdapter
    from app.apps.trading.research.issuer_identity import SecIssuerIdentityResolver

    assert SecEdgarAdapter._headers()["User-Agent"] == "MarketPulse ana@example.com"
    assert SecIssuerIdentityResolver._headers() == {
        "User-Agent": "MarketPulse ana@example.com", "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov",
    }
    assert seen == [{"User-Agent": "MarketPulse ana@example.com", "Accept-Encoding": "gzip, deflate"}]


class NoContactSource:
    def tickers(self):
        raise SecContactRequired()

    def submissions(self, cik):
        raise SecContactRequired()


class Repository:
    def __init__(self):
        self.rows: dict = {}

    def get(self, keys):
        return {} if isinstance(keys, list) else None

    def save(self, *args):
        self.rows[args[0]] = args[1:]

    def shares_fetched_at(self):
        return NOW


def test_financials_without_a_contact_ask_for_an_email() -> None:
    profiles = CompanyProfiles(repository_factory=Repository, source_factory=NoContactSource, clock=lambda: NOW)
    service = FinancialsService(repository_factory=Repository, source_factory=NoContactSource, profiles=lambda: profiles,
                                price_of=lambda instrument: None, clock=lambda: NOW)
    app = FastAPI()
    app.include_router(create_trading_fundamentals_router(lambda: service))
    response = TestClient(app).get("/api/trading/fundamentals", params={"instrument_id": "equity:NASDAQ:AAPL"})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "sec_contact_required", "message": sec_contact.CONTACT_REQUIRED_MESSAGE}


def test_events_without_a_contact_still_show_dividends_and_are_not_cached() -> None:
    class Profiles:
        def ensure(self, tickers, max_new=1):
            raise SecContactRequired()

    class Alpaca:
        def actions(self, tickers, start, end):
            return {"AAPL": [CorporateEvent(kind="dividend", date=date(2026, 8, 11), amount=0.26)]}

    repository = Repository()
    service = CorporateEventsService(repository_factory=lambda: repository, sec_factory=NoContactSource, alpaca_factory=Alpaca,
                                     profiles=Profiles, clock=lambda: NOW)
    assert [event.kind for event in service.company("equity:NASDAQ:AAPL").events] == ["dividend"]
    assert repository.rows == {}


def test_a_missing_contact_is_not_mistaken_for_a_company_without_filings() -> None:
    class Source:
        def tickers(self):
            return {"AAPL": ("0000320193", "Apple Inc.")}

        def submissions(self, cik):
            raise SecContactRequired()

    class Profiles(Repository):
        def save_profile(self, *args):
            raise AssertionError("nothing to save")

    profiles = CompanyProfiles(repository_factory=Profiles, source_factory=Source, clock=lambda: NOW)
    with pytest.raises(SecContactRequired):
        profiles.ensure(["AAPL"])
