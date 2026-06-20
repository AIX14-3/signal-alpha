import unittest
import warnings
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.signals import get_database_pool
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app


class FakeConnection:
    def __init__(self, *, detail_row=None):
        self.detail_row = detail_row
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
        if "final_signals.id = $1" in sql:
            return self.detail_row
        return {
            "id": 7,
            "ticker": args[0],
            "name": "삼성전자",
            "signal": "neutral",
            "summary": "중립 신호",
        }


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


class SignalRouteTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection(detail_row=_signal_detail_row())
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

    def test_get_signal_by_ticker_returns_current_signal(self):
        response = self.client.get("/signals/005930")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ticker"], "005930")
        self.assertEqual(response.json()["signal"], "neutral")

    def test_get_signal_detail_requires_authentication(self):
        response = self.client.get("/api/signals/200")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_get_signal_detail_returns_sources_and_evidence(self):
        response = self.client.get("/api/signals/200", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["signal_id"], 200)
        self.assertEqual(body["stock"]["stock_code"], "005930")
        self.assertEqual(body["direction"], "neutral")
        self.assertEqual(body["score"], 50)
        self.assertEqual(body["alignment_rate"], 0.5)
        self.assertEqual(body["data_status"], "partial")
        self.assertTrue(body["needs_review"])
        self.assertIn("데이터 방향성", body["notice"])

        sources = {source["source"]: source for source in body["sources"]}
        self.assertEqual(set(sources), {"DART", "PRICE", "REPORT", "ALTERNATIVE"})
        self.assertEqual(sources["DART"]["score"], 50)
        self.assertEqual(sources["DART"]["data_status"], "ok")
        self.assertEqual(sources["DART"]["summary"], "공시 기반 중립 방향성입니다.")
        self.assertEqual(sources["DART"]["evidence"][0]["title"], "분기보고서")
        self.assertEqual(sources["PRICE"]["data_status"], "missing")
        self.assertEqual(sources["PRICE"]["evidence"], [])

    def test_get_signal_detail_returns_404_when_signal_missing(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection(detail_row=None))

        response = self.client.get("/api/signals/999", headers=self.auth_headers())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_NOT_FOUND")


def _signal_detail_row():
    return {
        "id": 200,
        "stock_id": 10,
        "analysis_result_id": 300,
        "signal_date": "2026-06-19",
        "run_key": "AGGREGATED",
        "version": "final-agg-v1",
        "final_score": Decimal("50.00"),
        "confidence": Decimal("50.00"),
        "signal": "neutral",
        "source_agreement": "LOW",
        "warning_level": "CAUTION",
        "score_breakdown": {
            "DART": {
                "direction": "neutral",
                "score": 0.0,
                "score_100": 50.0,
                "data_status": "ok",
                "needs_review": False,
                "agent_result_id": 400,
            },
            "PRICE": {
                "direction": "unknown",
                "score": None,
                "score_100": None,
                "data_status": "missing",
                "needs_review": True,
            },
        },
        "summary": "DART 데이터는 중립 방향성을 보이며 추가 확인이 필요합니다.",
        "bull_point": None,
        "bear_point": None,
        "needs_review": True,
        "is_published": True,
        "published_at": datetime(2026, 6, 19, tzinfo=UTC),
        "created_at": datetime(2026, 6, 19, tzinfo=UTC),
        "consensus_score": Decimal("50.00"),
        "positive_evidence": [],
        "caution_evidence": [{"source": "PRICE", "risk_flags": ["missing_source"]}],
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "sector": "반도체",
        "analysis_date": "2026-06-19",
        "analysis_mode": "full",
        "analysis_run_key": "AGGREGATED",
        "analysis_version": "final-agg-v1",
        "base_score": Decimal("50.00"),
        "analysis_warning": "missing_source",
        "source_signal_event_ids": [501],
        "agent_results": [
            {
                "id": 400,
                "debate_method": "D-1",
                "method_score": 50,
                "method_signal": "neutral",
                "method_detail": {
                    "source": "DART",
                    "source_score": 0.0,
                    "summary": "공시 기반 중립 방향성입니다.",
                    "data_status": "ok",
                    "risk_flags": [],
                    "needs_review": False,
                },
                "source_signal_event_ids": [501],
                "reliability_score": 90,
                "evidence_quality": 100,
                "llm_model": None,
                "prompt_ver": "dart-rules-v1",
                "created_at": datetime(2026, 6, 19, tzinfo=UTC),
            }
        ],
        "signal_events": [
            {
                "id": 501,
                "source_document_id": 601,
                "source_type": "DART",
                "event_type": "periodic_report",
                "event_date": "2026-06-19",
                "signal_direction": "neutral",
                "impact_level": "medium",
                "title": "분기보고서",
                "summary": "정기 공시가 확인되었습니다.",
                "evidence_url": "https://dart.fss.or.kr/example",
                "needs_review": False,
                "source_name": "DART",
                "source_url": "https://dart.fss.or.kr/example",
                "is_official": True,
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
