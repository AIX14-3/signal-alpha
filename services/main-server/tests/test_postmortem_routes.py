"""FR-7 부검 라우트 배선 — /trades·/patterns 가 narrative 를 올바르게 붙이는지(라우트 레벨).

리포지토리·구독·워커 프록시를 페이크로 대체해, 부검 계산 자체가 아니라 **narrative 첨부 계약**을
검증한다: 라운드트립이 있으면 첨부, 빈 결과엔 헛호출 안 함, 패턴 소표본은 억제(내러티브 없음).
"""

from __future__ import annotations

import unittest
import warnings
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.postmortem import get_current_user, get_database_pool
from app.main import app

_MODULE = "app.api.routes.postmortem"
_UTC = timezone.utc


def _fill(side: str, day: int, ticker: str = "005930", stock_id: int = 7) -> dict:
    return {
        "side": side,
        "filled_at": datetime(2026, 3, day, tzinfo=_UTC),
        "quantity": "10",
        "price": "100",
        "fee": None,
        "ticker": ticker,
        "stock_id": stock_id,
    }


def _roundtrip_fills(ticker: str, stock_id: int) -> list[dict]:
    return [_fill("buy", 1, ticker, stock_id), _fill("sell", 5, ticker, stock_id)]


class _FakeAcquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


class _FakeStockRepo:
    def __init__(self, _conn):
        pass

    async def get_by_ticker(self, ticker: str):
        return {"id": 7, "name": "삼성전자", "ticker": ticker}


class _FakeFillsRepo:
    rows: list[dict] = []

    def __init__(self, _conn):
        pass

    async def list_fills(self, **_kwargs):
        return type(self).rows


class _FakePlanRepo:
    def __init__(self, _conn):
        pass

    async def get_plan(self, **_kwargs):
        return None


class _FakeOverlayRepo:
    def __init__(self, _conn):
        pass

    async def list_by_stock(self, **_kwargs):
        return []


class PostmortemRoutesTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_user] = lambda: {"id": 1}
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        self._patchers = [
            patch(f"{_MODULE}._subscription_active", new=AsyncMock(return_value=True)),
            patch(f"{_MODULE}.StockRepository", new=_FakeStockRepo),
            patch(f"{_MODULE}.UserTradeFillsRepository", new=_FakeFillsRepo),
            patch(f"{_MODULE}.UserTradePlanRepository", new=_FakePlanRepo),
            patch(f"{_MODULE}.UserTradeSignalOverlayRepository", new=_FakeOverlayRepo),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        app.dependency_overrides.clear()
        _FakeFillsRepo.rows = []

    def test_trades_attaches_narrative(self):
        _FakeFillsRepo.rows = _roundtrip_fills("005930", 7)
        narr = {"summary": "복기 요약", "key_facts": ["단서"], "model": "m"}
        with patch(f"{_MODULE}._postmortem_narrative", new=AsyncMock(return_value=narr)) as mock:
            response = TestClient(app).get("/api/postmortem/trades/005930")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["round_trips"]), 1)
        self.assertEqual(body["narrative"]["summary"], "복기 요약")
        mock.assert_awaited_once()

    def test_trades_no_fills_skips_narrative_call(self):
        _FakeFillsRepo.rows = []
        with patch(f"{_MODULE}._postmortem_narrative", new=AsyncMock(return_value={"x": 1})) as mock:
            response = TestClient(app).get("/api/postmortem/trades/005930")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["round_trips"], [])
        self.assertIsNone(body["narrative"])
        mock.assert_not_awaited()

    def test_patterns_suppressed_has_no_narrative(self):
        # 라운드트립 1건(<5) → 억제. 내러티브 호출·키 없음.
        _FakeFillsRepo.rows = _roundtrip_fills("005930", 7)
        with patch(f"{_MODULE}._postmortem_narrative", new=AsyncMock(return_value={"x": 1})) as mock:
            response = TestClient(app).get("/api/postmortem/patterns")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["suppressed"])
        self.assertNotIn("narrative", body)
        mock.assert_not_awaited()

    def test_patterns_attaches_narrative_above_threshold(self):
        rows: list[dict] = []
        for i in range(5):
            rows += _roundtrip_fills(f"00000{i}", 10 + i)
        _FakeFillsRepo.rows = rows
        narr = {"summary": "패턴 복기", "key_facts": [], "model": "m"}
        with patch(f"{_MODULE}._postmortem_narrative", new=AsyncMock(return_value=narr)) as mock:
            response = TestClient(app).get("/api/postmortem/patterns")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["suppressed"])
        self.assertEqual(body["narrative"]["summary"], "패턴 복기")
        mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
