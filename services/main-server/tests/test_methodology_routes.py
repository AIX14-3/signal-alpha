import unittest
import warnings
from decimal import Decimal

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.methodology import get_database_pool
from app.main import app


class FakeConnection:
    async def fetch(self, sql, *args):
        if "signal_journal_outcomes" not in sql:
            raise AssertionError(f"Unexpected fetch SQL: {sql}")
        return [
            {
                "horizon": "7td",
                "confirmed_count": 12,
                "aligned_count": 9,
                "not_aligned_count": 3,
                "pending_count": 4,
                "alignment_rate": Decimal("75.0"),
                "first_outcome_trade_date": "2026-06-22",
                "last_outcome_trade_date": "2026-07-07",
            },
            {
                "horizon": "30td",
                "confirmed_count": 0,
                "aligned_count": 0,
                "not_aligned_count": 0,
                "pending_count": 16,
                "alignment_rate": None,
                "first_outcome_trade_date": None,
                "last_outcome_trade_date": None,
            },
        ]


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return FakeAcquire(self.connection)


class MethodologyRoutesTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection())
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_posthoc_alignment_returns_journal_based_summary(self):
        response = self.client.get("/api/methodology/posthoc-alignment")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["scope"], "journal_based")
        self.assertEqual(body["metric_label"], "저널 기준 사후정합성")
        self.assertEqual(body["items"][0]["horizon"], "7td")
        self.assertEqual(body["items"][0]["alignment_rate"], 75.0)
        self.assertEqual(body["items"][0]["sample_status"], "표본 부족")
        self.assertEqual(body["items"][1]["sample_status"], "확정 대기")
        self.assertIn("데이터 방향성", body["methodology"]["basis"])
        self.assertIn("미래 결과를 보장하지 않습니다", body["notice"])


if __name__ == "__main__":
    unittest.main()
