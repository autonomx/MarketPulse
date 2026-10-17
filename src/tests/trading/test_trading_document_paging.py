from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.api import create_trading_router


class PagingRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def iter(self, record_type: str):
        return iter(())

    def list(self, record_type: str, *, limit: int = 100, after: tuple[str, str] | None = None):
        self.calls.append({"record_type": record_type, "limit": limit, "after": after})
        return [{
            "record_type": record_type,
            "record_id": f"r-{len(self.calls)}",
            "payload": {},
            "revision": 1,
            "status": "active",
            "updated_at": "2026-10-08 12:00:00+00:00",
        }]


def test_document_lists_page_with_an_updated_at_and_record_id_cursor() -> None:
    repository = PagingRepository()
    app = FastAPI()
    app.include_router(create_trading_router(lambda: repository))
    client = TestClient(app)

    first = client.get("/api/trading/drawings", params={"limit": 500}).json()["records"]
    assert repository.calls[0] == {"record_type": "drawing", "limit": 500, "after": None}
    client.get(
        "/api/trading/drawings",
        params={"limit": 500, "after_updated_at": first[-1]["updated_at"], "after_record_id": first[-1]["record_id"]},
    )
    assert repository.calls[1] == {
        "record_type": "drawing",
        "limit": 500,
        "after": ("2026-10-08 12:00:00+00:00", "r-1"),
    }
