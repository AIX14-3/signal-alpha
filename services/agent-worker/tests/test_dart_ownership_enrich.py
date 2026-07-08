"""DART 세부변동내역 enrich(거래유형·단가) + 정규화 장내-only 게이트 테스트."""

import unittest

from app.orchestrator.dart.ownership_sync import DartOwnershipSyncService
from app.orchestrator.dart.tasks import _ownership_direction


class _FakeRepo:
    def __init__(self, candidates):
        self._candidates = candidates
        self.updates: list[tuple] = []
        self.list_calls: list[int] = []

    async def upsert_events(self, entries):
        return len(entries)

    async def list_unenriched_ownership(self, *, stock_id, limit):
        self.list_calls.append(limit)
        return self._candidates[:limit]

    async def update_trade_detail(self, *, corp_code, rcept_no, trade_type, unit_price):
        self.updates.append((corp_code, rcept_no, trade_type, unit_price))


class _FakeCollector:
    def __init__(self, mapping, fail=()):
        self._mapping = mapping
        self._fail = set(fail)

    async def collect(self, *, stock_code):
        return []

    async def fetch_trade_detail(self, *, rcept_no):
        if rcept_no in self._fail:
            raise RuntimeError("network")
        return self._mapping[rcept_no]


class EnrichTradeDetailTest(unittest.IsolatedAsyncioTestCase):
    async def test_updates_enriched_and_skips_network_failures(self):
        repo = _FakeRepo([
            {"corp_code": "C", "rcept_no": "R1"},
            {"corp_code": "C", "rcept_no": "R2"},
        ])
        collector = _FakeCollector({"R1": ("onmarket_buy", 73400.0)}, fail={"R2"})
        svc = DartOwnershipSyncService(collector=collector, repository=repo)

        result = await svc.enrich_trade_detail(stock_id=1)

        self.assertEqual(result, {"enriched": 1, "failed": 1, "candidates": 2})
        self.assertEqual(repo.updates, [("C", "R1", "onmarket_buy", 73400.0)])

    async def test_bounded_by_detail_limit(self):
        repo = _FakeRepo([{"corp_code": "C", "rcept_no": f"R{i}"} for i in range(100)])
        collector = _FakeCollector({f"R{i}": ("onmarket_buy", None) for i in range(100)})
        svc = DartOwnershipSyncService(collector=collector, repository=repo, detail_limit=5)

        result = await svc.enrich_trade_detail(stock_id=1)

        self.assertEqual(repo.list_calls, [5])  # 상한 전달
        self.assertEqual(result["candidates"], 5)
        self.assertEqual(result["enriched"], 5)

    async def test_sync_ticker_runs_enrich_when_stock_id_present(self):
        repo = _FakeRepo([{"corp_code": "C", "rcept_no": "R1"}])
        collector = _FakeCollector({"R1": ("gift", None)})
        svc = DartOwnershipSyncService(collector=collector, repository=repo)

        out = await svc.sync_ticker(stock_code="005930", stock_id=1)

        self.assertEqual(out["detail_enriched"]["enriched"], 1)


class OwnershipDirectionGateTest(unittest.TestCase):
    def test_onmarket_buy_keeps_positive(self):
        self.assertEqual(
            _ownership_direction({"trade_type": "onmarket_buy", "shares_delta": 100}), "positive"
        )

    def test_onmarket_sell_keeps_negative(self):
        self.assertEqual(
            _ownership_direction({"trade_type": "onmarket_sell", "shares_delta": -100}), "negative"
        )

    def test_gift_forced_neutral_even_if_increase(self):
        # 증여로 지분이 늘어도(shares_delta>0) 시장 확신이 아니므로 중립.
        self.assertEqual(
            _ownership_direction({"trade_type": "gift", "shares_delta": 100}), "neutral"
        )

    def test_stock_option_forced_neutral(self):
        self.assertEqual(
            _ownership_direction({"trade_type": "stock_option", "shares_delta": 100}), "neutral"
        )

    def test_mixed_forced_neutral(self):
        self.assertEqual(
            _ownership_direction({"trade_type": "mixed", "shares_delta": 100}), "neutral"
        )

    def test_none_trade_type_uses_delta_sign_b_lite(self):
        # 미enrich(None) → 기존 shares_delta 부호 거동.
        self.assertEqual(_ownership_direction({"trade_type": None, "shares_delta": -5}), "negative")
        self.assertEqual(_ownership_direction({"shares_delta": 5}), "positive")


if __name__ == "__main__":
    unittest.main()
