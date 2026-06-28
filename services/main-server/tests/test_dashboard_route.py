import unittest
import warnings
from datetime import UTC, datetime, timedelta

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.dashboard import get_database_pool
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app


class FakeConnection:
    def __init__(self, *, with_signal=True, with_journal=True):
        self.with_signal = with_signal
        self.with_journal = with_journal
        self.users_by_id = {
            1: {
                "id": 1,
                "email": "user@example.com",
                "nickname": "사용자",
                "agreed_risk": True,
                "is_verified": False,
            }
        }

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM watchlists" in sql and "INNER JOIN api.stocks" in sql:
            return [
                {
                    "id": 101,
                    "user_id": 1,
                    "stock_id": 10,
                    "ticker": "005930",
                    "name": "삼성전자",
                    "market": "KOSPI",
                    "sector": "반도체",
                    "notification_enabled": False,
                    "created_at": datetime(2026, 6, 18, tzinfo=UTC),
                }
            ]
        if "FROM api.signals_current" in sql:
            if not self.with_signal:
                return []
            return [
                {
                    "id": 200,
                    "stock_id": 10,
                    "signal": "positive",
                    "final_score": 72,
                    "source_agreement": "HIGH",
                    "summary": "공식 데이터에서 같은 방향성이 확인되었습니다.",
                    "needs_review": False,
                    "published_at": datetime(2026, 6, 18, tzinfo=UTC),
                    "created_at": datetime(2026, 6, 18, tzinfo=UTC),
                    "score_breakdown": {
                        # worker 는 score(부호 -1~1) + score_100(0~100) 을 함께 싣고,
                        # 미산출 소스도 data_status="missing" placeholder 로 채운다.
                        "DART": {"direction": "neutral", "score": 0.0, "score_100": 50, "data_status": "ok"},
                        "PRICE": {"direction": "positive", "score": 0.44, "score_100": 72, "data_status": "ok"},
                        "HIRING": {"direction": "positive", "score": 0.28, "score_100": 64, "data_status": "ok"},
                        "REPORT": {"direction": "unknown", "score": None, "score_100": None, "data_status": "missing"},
                    },
                    "consensus_score": 80,
                }
            ]
        if "FROM signal_journals" in sql:
            if not self.with_journal:
                return []
            return [
                {
                    "id": 300,
                    "stock_id": 10,
                    "final_signal_id": 200,
                    "created_at": datetime(2026, 6, 18, tzinfo=UTC),
                }
            ]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")


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


class DashboardRouteTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(self.connection)
        self.client = TestClient(app)
        self.token = create_access_token(
            user_id=1,
            email="user@example.com",
            secret_key=get_settings().auth_secret_key,
            expires_delta=timedelta(minutes=30),
        )

    def tearDown(self):
        app.dependency_overrides.clear()

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_dashboard_requires_authentication(self):
        response = self.client.get("/api/dashboard")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_dashboard_returns_watchlist_signal_source_summary_and_journal_state(self):
        response = self.client.get("/api/dashboard", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["email"], "user@example.com")
        self.assertEqual(body["watchlist_count"], 1)
        item = body["items"][0]
        self.assertEqual(item["stock"]["stock_code"], "005930")
        self.assertEqual(item["latest_signal"]["signal_id"], 200)
        self.assertEqual(item["latest_signal"]["direction"], "positive")
        self.assertEqual(item["latest_signal"]["alignment_rate"], 0.8)
        self.assertFalse(item["latest_signal"]["needs_review"])
        # 기여 소스만 센다: DART/PRICE/HIRING(ok) = 3. REPORT(missing placeholder)·PATENT/DATALAB(미존재) 제외.
        self.assertEqual(item["latest_signal"]["source_count"], 3)
        sources = {source["source"]: source for source in item["source_summary"]}
        self.assertEqual(set(sources), {"DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB"})
        self.assertEqual(sources["DART"]["data_status"], "ok")
        self.assertEqual(sources["REPORT"]["data_status"], "missing")
        # 평탄 HIRING 키가 읽힌다(과거 ALTERNATIVE 중첩 → 깨졌던 경로). PATENT 는 미존재 → missing.
        self.assertEqual(sources["HIRING"]["data_status"], "ok")
        # 0~100 스케일(score_100=64)을 노출 — 부호 score(0.28)가 아님.
        self.assertEqual(sources["HIRING"]["score"], 64)
        self.assertEqual(sources["PATENT"]["data_status"], "missing")
        self.assertEqual(item["journal"], {"has_journal": True, "latest_journal_id": 300})
        self.assertIn("데이터 방향성", body["notice"])

    def test_dashboard_marks_missing_signal_and_sources(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(
            FakeConnection(with_signal=False, with_journal=False)
        )

        response = self.client.get("/api/dashboard", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["latest_signal"]["data_status"], "missing")
        self.assertIsNone(item["latest_signal"]["signal_id"])
        self.assertTrue(
            all(source["data_status"] == "missing" for source in item["source_summary"])
        )
        self.assertEqual(item["journal"], {"has_journal": False, "latest_journal_id": None})


if __name__ == "__main__":
    unittest.main()
