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


_MISSING = object()


class FakeConnection:
    def __init__(self, *, current_row=_MISSING, detail_row=None, list_rows=None):
        self.current_row = _current_signal_row() if current_row is _MISSING else current_row
        self.detail_row = detail_row
        self.list_rows = list_rows or []
        self.fetch_calls = []
        self.reads = []
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
        if "api.signal_detail" in sql:
            return self.detail_row
        if "api.signals_current" in sql and "ticker = $1" in sql:
            return self.current_row
        if "INSERT INTO user_signal_reads" in sql:
            row = {
                "id": len(self.reads) + 1,
                "user_id": args[0],
                "final_signal_id": args[1],
                "read_at": datetime(2026, 6, 23, tzinfo=UTC),
                "read_date": "2026-06-23",
            }
            self.reads.append(row)
            return row
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "stock_id = ANY" in sql:
            wanted = set(args[0])
            return [row for row in self.list_rows if row["stock_id"] in wanted]
        return self.list_rows


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

    def test_get_signal_by_ticker_requires_authentication(self):
        response = self.client.get("/signals/005930")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_get_signal_by_ticker_returns_curated_signal(self):
        response = self.client.get("/signals/005930", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        # 큐레이션 응답만 노출 — 원시 내부 컬럼(score_breakdown/ml_* 등)은 직접 노출되지 않는다.
        self.assertEqual(body["stock"]["stock_code"], "005930")
        self.assertEqual(body["direction"], "neutral")
        self.assertNotIn("score_breakdown", body)

    def test_get_signal_by_stock_requires_authentication(self):
        response = self.client.get("/api/signals/by-stock/005930")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_get_signal_by_stock_returns_current_signal_summary(self):
        response = self.client.get("/api/signals/by-stock/005930", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["signal_id"], 7)
        self.assertEqual(body["stock"]["stock_code"], "005930")
        self.assertEqual(body["stock"]["stock_name"], "삼성전자")
        self.assertEqual(body["direction"], "neutral")
        self.assertEqual(body["score"], 50)
        self.assertEqual(body["alignment_rate"], 0.5)
        self.assertEqual(body["data_status"], "partial")
        self.assertTrue(body["needs_review"])
        self.assertIn("데이터 방향성", body["notice"])

    def test_get_signal_by_stock_returns_404_when_missing(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection(current_row=None))

        response = self.client.get("/api/signals/by-stock/999999", headers=self.auth_headers())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_NOT_FOUND")

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
        self.assertEqual(
            set(sources), {"DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB"}
        )
        self.assertEqual(sources["DART"]["score"], 50)
        self.assertEqual(sources["DART"]["data_status"], "ok")
        self.assertEqual(sources["DART"]["summary"], "공시 기반 중립 방향성입니다.")
        self.assertEqual(sources["DART"]["evidence"][0]["title"], "분기보고서")
        self.assertEqual(sources["PRICE"]["data_status"], "missing")
        self.assertEqual(sources["PRICE"]["evidence"], [])

    def test_list_signals_requires_authentication(self):
        response = self.client.get("/api/signals")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_list_signals_groups_sources_per_stock(self):
        connection = FakeConnection(list_rows=_signal_list_rows())
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)

        response = self.client.get("/api/signals", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)  # 소스 3행 → 종목당 1개
        item = body[0]
        self.assertEqual(item["stock"]["stock_code"], "005930")
        self.assertEqual(item["stock"]["stock_name"], "삼성전자")
        self.assertEqual(item["direction"], "POSITIVE")  # 대문자, positive 2 vs neutral 1
        self.assertEqual(item["score"], 75.0)  # (80+50+95)/3
        self.assertEqual(item["alignment_rate"], 0.6)  # 평균 consensus 60 / 100
        self.assertEqual(item["source_agreement"], "LOW")  # 가장 보수적(낮은 합의)
        self.assertEqual(item["warning_level"], "WARNING")  # 가장 보수적
        self.assertEqual(item["data_status"], "failed")  # WARNING → failed
        self.assertEqual(item["summary"], "채용 신호 요약")  # 기준행(첫 행) summary
        alternative = item["score_breakdown"]["alternative"]
        self.assertEqual(set(alternative), {"hiring", "patent", "datalab"})
        self.assertEqual(alternative["hiring"]["score"], 80)
        self.assertEqual(alternative["patent"]["direction"], "NEUTRAL")  # 대문자

    def test_list_signals_with_stock_ids_filter(self):
        connection = FakeConnection(list_rows=_signal_list_rows() + _signal_list_rows(stock_id=20, ticker="000660"))
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)

        response = self.client.get("/api/signals?stock_ids=10", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item["stock_id"] for item in body], [10])
        self.assertTrue(any("stock_id = ANY" in sql for sql, _ in connection.fetch_calls))

    def test_list_signals_blank_stock_ids_returns_empty(self):
        connection = FakeConnection(list_rows=_signal_list_rows())
        app.dependency_overrides[get_database_pool] = lambda: FakePool(connection)

        response = self.client.get("/api/signals?stock_ids=abc", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_signal_detail_returns_404_when_signal_missing(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection(detail_row=None))

        response = self.client.get("/api/signals/999", headers=self.auth_headers())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_NOT_FOUND")

    def test_mark_signal_read_requires_authentication(self):
        response = self.client.post("/api/signals/200/read")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_mark_signal_read_records_user_read_state(self):
        response = self.client.post("/api/signals/200/read", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "read")
        self.assertEqual(body["signal_id"], 200)
        self.assertEqual(body["read_at"], "2026-06-23T00:00:00+00:00")
        self.assertIn("데이터 방향성", body["notice"])
        self.assertEqual(self.connection.reads[0]["user_id"], 1)
        self.assertEqual(self.connection.reads[0]["final_signal_id"], 200)

    def test_mark_signal_read_returns_404_when_signal_missing(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool(FakeConnection(detail_row=None))

        response = self.client.post("/api/signals/999/read", headers=self.auth_headers())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_NOT_FOUND")


def _signal_list_rows(stock_id=10, ticker="005930"):
    # 한 종목에 소스별(run_key) 3행 — 모델 B 적재를 그대로 흉내낸다.
    base = {"stock_id": stock_id, "ticker": ticker, "name": "삼성전자", "market": "KOSPI"}
    return [
        {**base, "run_key": "HIRING", "final_score": 80, "signal": "positive", "warning_level": "NORMAL", "needs_review": False, "source_agreement": "HIGH", "consensus_score": 70, "summary": "채용 신호 요약"},
        {**base, "run_key": "PATENT", "final_score": 50, "signal": "neutral", "warning_level": "NORMAL", "needs_review": False, "source_agreement": "MEDIUM", "consensus_score": 60, "summary": None},
        {**base, "run_key": "DATALAB", "final_score": 95, "signal": "positive", "warning_level": "WARNING", "needs_review": True, "source_agreement": "LOW", "consensus_score": 50, "summary": None},
    ]


def _current_signal_row():
    return {
        "id": 7,
        "stock_id": 10,
        "ticker": "005930",
        "name": "삼성전자",
        "market": "KOSPI",
        "signal": "neutral",
        "final_score": Decimal("50.00"),
        "confidence": Decimal("50.00"),
        "consensus_score": Decimal("50.00"),
        "source_agreement": "LOW",
        "warning_level": "CAUTION",
        "needs_review": True,
        "summary": "중립 신호",
        "score_breakdown": {},
        "published_at": datetime(2026, 6, 23, tzinfo=UTC),
        "created_at": datetime(2026, 6, 23, tzinfo=UTC),
        "analysis_mode": "full",
        "base_score": Decimal("50.00"),
        "analysis_warning": "missing_source",
    }


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


class SignalListItemLatestPerSourceTest(unittest.TestCase):
    """종목 카드의 점수/소스별 값은 **각 소스의 최신 행**만으로 만들어야 한다.

    ``api.signals_current`` 가 과거 signal_date 행도 함께 들고 있어(is_current 는
    (stock_id, signal_date, run_key) 유일), 순서에 기대면 오래된 행이 남고 점수는
    날짜를 가로질러 평균돼 "어느 날짜의 값도 아닌 수"가 나온다.
    """

    @staticmethod
    def _row(run_key: str, signal_date: str, score: float, signal: str = "neutral") -> dict:
        from datetime import date as _date

        year, month, day = (int(p) for p in signal_date.split("-"))
        return {
            "stock_id": 1,
            "run_key": run_key,
            "signal_date": _date(year, month, day),
            "published_at": None,
            "created_at": None,
            "final_score": Decimal(str(score)),
            "signal": signal,
            "warning_level": "NORMAL",
            "source_agreement": "HIGH",
            "consensus_score": None,
            "confidence": None,
            "needs_review": False,
            "ticker": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "summary": "요약",
        }

    def test_per_source_value_is_the_newest_row_not_the_last_one_seen(self):
        from app.api.routes.signals import _signal_list_item

        # 오래된 행이 뒤에 오도록 섞어 넣는다(예전 구현은 마지막 행으로 덮어써 78.05 를 남겼다).
        rows = [
            self._row("HIRING", "2026-07-09", 50.0),
            self._row("HIRING", "2026-07-07", 78.05),
            self._row("DATALAB", "2026-07-09", 78.45),
            self._row("DATALAB", "2026-07-07", 86.65),
            self._row("PATENT", "2026-07-09", 46.65),
        ]

        item = _signal_list_item(1, rows)
        alternative = item["score_breakdown"]["alternative"]

        self.assertEqual(alternative["hiring"]["score"], 50.0)
        self.assertEqual(alternative["datalab"]["score"], 78.45)
        self.assertEqual(alternative["patent"]["score"], 46.65)

    def test_score_averages_only_the_newest_row_of_each_source(self):
        from app.api.routes.signals import _signal_list_item

        rows = [
            self._row("HIRING", "2026-07-09", 50.0),
            self._row("HIRING", "2026-07-07", 78.05),
            self._row("DATALAB", "2026-07-09", 78.45),
            self._row("DATALAB", "2026-07-07", 86.65),
            self._row("PATENT", "2026-07-09", 46.65),
        ]

        item = _signal_list_item(1, rows)

        # (50.00 + 78.45 + 46.65) / 3 = 58.37 — 이력을 섞으면 62.83 같은 무의미한 수가 나온다.
        self.assertEqual(item["score"], 58.37)

    def test_aggregated_run_key_is_not_folded_into_the_alternative_average(self):
        from app.api.routes.signals import _signal_list_item

        rows = [
            self._row("AGGREGATED", "2026-07-09", 48.35),
            self._row("PATENT", "2026-07-09", 46.65),
        ]

        item = _signal_list_item(1, rows)

        self.assertEqual(item["score"], 46.65)
        self.assertIsNone(item["score_breakdown"]["alternative"]["hiring"])


if __name__ == "__main__":
    unittest.main()
