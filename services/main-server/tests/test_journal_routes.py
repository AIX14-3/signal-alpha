import json
import unittest
import warnings
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.core.database import get_database_pool
from app.core.security import create_access_token
from app.main import app


class FakeConnection:
    def __init__(self):
        self.users_by_id = {
            1: {
                "id": 1,
                "email": "user@example.com",
                "nickname": "사용자",
                "agreed_risk": True,
                "is_verified": False,
            },
            2: {
                "id": 2,
                "email": "other@example.com",
                "nickname": "타사용자",
                "agreed_risk": True,
                "is_verified": False,
            },
        }
        # 저널은 전체 구독 전용 — 기본은 user 1/2 모두 활성 구독.
        self.subscribed_user_ids = {1, 2}
        self.stocks_by_ticker = {
            "005930": {
                "id": 10,
                "ticker": "005930",
                "name": "삼성전자",
                "market": "KOSPI",
                "sector": "반도체",
                "is_active": True,
            }
        }
        self.signal = {
            "id": 200,
            "stock_id": 10,
            "ticker": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "sector": "반도체",
            "signal": "neutral",
            "final_score": Decimal("50.00"),
            "source_agreement": "LOW",
            "warning_level": "CAUTION",
            "summary": "DART 데이터 방향성은 중립입니다.",
            "score_breakdown": {},
            "consensus_score": Decimal("50.00"),
            "needs_review": True,
            "is_published": True,
            "published_at": datetime(2026, 6, 22, tzinfo=UTC),
            "created_at": datetime(2026, 6, 22, tzinfo=UTC),
            "agent_results": [],
            "signal_events": [],
        }
        # 종목이 다른 시그널 — SIGNAL_STOCK_MISMATCH 검증용.
        self.other_stock_signal = {**self.signal, "id": 201, "stock_id": 99}
        self.journals = []
        self.next_journal_id = 20
        # stock_id → [{trade_date, close_price}] (러너가 동기화한 차트 시리즈 대역).
        self.chart_prices = {}

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM signal_subscriptions" in sql:
            if args[0] in self.subscribed_user_ids:
                return {
                    "id": 1,
                    "user_id": args[0],
                    "status": "active",
                    "expires_at": None,
                    "plan_type": "monthly_9900",
                }
            return None
        if "FROM api.stocks" in sql and "WHERE ticker = $1" in sql:
            return self.stocks_by_ticker.get(args[0])
        if "api.signal_detail" in sql:
            for signal in (self.signal, self.other_stock_signal):
                if args[0] == signal["id"]:
                    return signal
            return None
        if "INSERT INTO signal_journals" in sql:
            stock = next(
                stock for stock in self.stocks_by_ticker.values() if stock["id"] == args[2]
            )
            journal = {
                "id": self.next_journal_id,
                "user_id": args[0],
                "final_signal_id": args[1],
                "stock_id": args[2],
                "user_view": args[3],
                "user_memo": args[4],
                "tags": args[5],
                "signal_score_at_time": args[6],
                "signal_value_at_time": args[7],
                "source_agreement_at_time": args[8],
                "retrospective_memo": None,
                "retro_outcome_class": None,
                "outcomes": "[]",
                "created_at": datetime(2026, 6, 22, tzinfo=UTC),
                "updated_at": datetime(2026, 6, 22, tzinfo=UTC),
                "ticker": stock["ticker"],
                "name": stock["name"],
                "market": stock["market"],
                "sector": stock["sector"],
            }
            self.next_journal_id += 1
            self.journals.append(journal)
            return journal
        if "FROM signal_journals" in sql and "signal_journals.id = $1" in sql:
            journal = next(
                (
                    journal
                    for journal in self.journals
                    if journal["id"] == args[0] and journal["user_id"] == args[1]
                ),
                None,
            )
            return journal
        if "UPDATE signal_journals" in sql:
            journal = next(
                (
                    journal
                    for journal in self.journals
                    if journal["id"] == args[0] and journal["user_id"] == args[1]
                ),
                None,
            )
            if journal is None:
                return None
            journal["user_view"] = args[2]
            journal["user_memo"] = args[3]
            journal["tags"] = args[4]
            journal["retrospective_memo"] = args[5]
            journal["retro_outcome_class"] = args[6]
            journal["updated_at"] = datetime(2026, 6, 23, tzinfo=UTC)
            return journal
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM signal_journal_chart_prices" in sql:
            return [
                row
                for row in self.chart_prices.get(args[0], [])
                if row["trade_date"] >= args[1]
            ]
        if "FROM signal_journals" in sql:
            rows = [journal for journal in self.journals if journal["user_id"] == args[0]]
            if "stocks.ticker = $2" in sql:
                rows = [journal for journal in rows if journal["ticker"] == args[1]]
            return rows[: args[-1]]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def execute(self, sql, *args):
        if "DELETE FROM signal_journals" in sql:
            before = len(self.journals)
            self.journals = [
                journal
                for journal in self.journals
                if not (journal["id"] == args[0] and journal["user_id"] == args[1])
            ]
            return f"DELETE {before - len(self.journals)}"
        raise AssertionError(f"Unexpected execute SQL: {sql}")


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


class JournalRoutesTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(self.connection)
        self.client = TestClient(app)
        self.token = self.token_for(1, "user@example.com")

    def tearDown(self):
        app.dependency_overrides.clear()

    def token_for(self, user_id, email):
        return create_access_token(
            user_id=user_id,
            email=email,
            secret_key=get_settings().auth_secret_key,
            expires_delta=timedelta(minutes=30),
        )

    def auth_headers(self, token=None):
        return {"Authorization": f"Bearer {token or self.token}"}

    def create_journal(self, **overrides):
        payload = {
            "stock_code": "005930",
            "final_signal_id": 200,
            "user_view": "research_more",
            "memo": "Report 데이터가 없어 추가 근거 확인이 필요합니다.",
            "tags": ["DART", "추가확인"],
        }
        payload.update(overrides)
        return self.client.post("/api/journals", json=payload, headers=self.auth_headers())

    def test_journals_require_authentication(self):
        response = self.client.get("/api/journals")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_journals_available_without_subscription(self):
        # 저널은 더 이상 구독 전용이 아니다 — 로그인만 하면 비구독자도 이용할 수 있다.
        self.connection.subscribed_user_ids.discard(1)

        create_response = self.create_journal()
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get("/api/journals", headers=self.auth_headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)

    def test_create_list_detail_update_and_delete_journal(self):
        create_response = self.create_journal()

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["journal_id"], 20)
        self.assertEqual(created["stock_code"], "005930")
        self.assertEqual(created["final_signal_id"], 200)
        self.assertEqual(created["user_view"], "research_more")
        self.assertEqual(created["tags"], ["DART", "추가확인"])
        self.assertEqual(created["signal_score_at_time"], 50.0)
        self.assertEqual(created["signal_value_at_time"], "neutral")
        self.assertEqual(created["source_agreement_at_time"], "LOW")
        self.assertEqual(created["outcomes"], [])
        self.assertIn("데이터 방향성", created["notice"])

        list_response = self.client.get("/api/journals?stock_code=005930", headers=self.auth_headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["count"], 1)
        self.assertEqual(list_response.json()["items"][0]["journal_id"], 20)

        detail_response = self.client.get("/api/journals/20", headers=self.auth_headers())
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["memo"], "Report 데이터가 없어 추가 근거 확인이 필요합니다.")

        patch_response = self.client.patch(
            "/api/journals/20",
            json={
                "user_view": "not_relevant",
                "memo": "현재 관심 기준에서는 낮은 관련도로 판단했습니다.",
                "tags": ["검토완료"],
            },
            headers=self.auth_headers(),
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["user_view"], "not_relevant")
        self.assertEqual(patch_response.json()["tags"], ["검토완료"])

        delete_response = self.client.delete("/api/journals/20", headers=self.auth_headers())
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json(), {"status": "deleted"})

    def test_journal_response_exposes_outcomes(self):
        self.create_journal()
        self.connection.journals[0]["outcomes"] = json.dumps(
            [
                {
                    "horizon": "7td",
                    "base_trade_date": "2026-06-22",
                    "base_price": 70000,
                    "outcome_trade_date": "2026-07-01",
                    "outcome_price": 73500,
                    "change_pct": 5.0,
                    "checked_at": "2026-07-02T06:00:00+09:00",
                }
            ]
        )

        response = self.client.get("/api/journals/20", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        outcomes = response.json()["outcomes"]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["horizon"], "7td")
        self.assertEqual(outcomes[0]["change_pct"], 5.0)

    def test_journal_chart_returns_series_and_change_since_created(self):
        self.create_journal()
        # 작성일(KST) = 6/22. 작성일 이후 최신 종가까지 시리즈 제공.
        self.connection.chart_prices[10] = [
            {"trade_date": date(2026, 6, 19), "close_price": Decimal("69000.00")},
            {"trade_date": date(2026, 6, 22), "close_price": Decimal("70000.00")},
            {"trade_date": date(2026, 6, 23), "close_price": Decimal("71000.00")},
            {"trade_date": date(2026, 6, 24), "close_price": Decimal("73500.00")},
        ]

        response = self.client.get("/api/journals/20/chart", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["journal"]["journal_id"], 20)
        chart = body["chart"]
        self.assertEqual(len(chart["series"]), 4)
        # 기준점 = 작성일 이하 가장 최근 거래일(6/22) 종가.
        self.assertEqual(chart["base_trade_date"], "2026-06-22")
        self.assertEqual(chart["base_price"], 70000.0)
        self.assertEqual(chart["latest_trade_date"], "2026-06-24")
        self.assertEqual(chart["latest_price"], 73500.0)
        self.assertEqual(chart["change_pct_since_created"], 5.0)
        self.assertIn("데이터 방향성", body["notice"])

    def test_journal_chart_before_sync_returns_empty_series(self):
        self.create_journal()

        response = self.client.get("/api/journals/20/chart", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        chart = response.json()["chart"]
        self.assertEqual(chart["series"], [])
        self.assertIsNone(chart["base_price"])
        self.assertIsNone(chart["change_pct_since_created"])

    def test_retrospective_requires_confirmed_outcome(self):
        self.create_journal()

        blocked = self.client.patch(
            "/api/journals/20",
            json={"retrospective_memo": "예상보다 흐름이 빨랐다."},
            headers=self.auth_headers(),
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["detail"]["code"], "RETROSPECTIVE_NOT_READY")

        # outcome 확정 후에는 회고 저장 가능.
        self.connection.journals[0]["outcomes"] = json.dumps(
            [{"horizon": "7td", "change_pct": 5.0}]
        )
        saved = self.client.patch(
            "/api/journals/20",
            json={"retrospective_memo": "예상보다 흐름이 빨랐다."},
            headers=self.auth_headers(),
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["retrospective_memo"], "예상보다 흐름이 빨랐다.")
        # 회고만 보냈을 때 기존 판단/메모는 유지된다.
        self.assertEqual(saved.json()["user_view"], "research_more")

    def test_structured_retrospective_requires_confirmed_outcome(self):
        # 경량 구조 회고(결과 분류)도 회고와 동일하게 변동 확정 게이트를 받는다.
        self.create_journal()

        blocked = self.client.patch(
            "/api/journals/20",
            json={"retro_outcome_class": "as_planned"},
            headers=self.auth_headers(),
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["detail"]["code"], "RETROSPECTIVE_NOT_READY")

    def test_structured_retrospective_saved_when_confirmed(self):
        self.create_journal()
        self.connection.journals[0]["outcomes"] = json.dumps(
            [{"horizon": "7td", "change_pct": 5.0}]
        )
        saved = self.client.patch(
            "/api/journals/20",
            json={
                "retro_outcome_class": "unexpected_good",
                "retrospective_memo": "다음엔 실적 시즌 가중.",
            },
            headers=self.auth_headers(),
        )
        self.assertEqual(saved.status_code, 200)
        body = saved.json()
        self.assertEqual(body["retro_outcome_class"], "unexpected_good")
        self.assertEqual(body["retrospective_memo"], "다음엔 실적 시즌 가중.")

    def test_blank_retrospective_normalized_to_null(self):
        # 빈 문자열 회고 메모는 NULL 로 정규화된다("" 저장 방지).
        self.create_journal()
        self.connection.journals[0]["outcomes"] = json.dumps(
            [{"horizon": "7td", "change_pct": 5.0}]
        )
        saved = self.client.patch(
            "/api/journals/20",
            json={"retro_outcome_class": "as_planned", "retrospective_memo": "   "},
            headers=self.auth_headers(),
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.json()["retrospective_memo"])

    def test_invalid_retro_outcome_rejected(self):
        self.create_journal()
        self.connection.journals[0]["outcomes"] = json.dumps(
            [{"horizon": "7td", "change_pct": 5.0}]
        )
        rejected = self.client.patch(
            "/api/journals/20",
            json={"retro_outcome_class": "moon_shot"},
            headers=self.auth_headers(),
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.json()["detail"]["code"], "INVALID_RETRO_OUTCOME")

    def test_journal_timeline_returns_series_with_markers(self):
        self.create_journal()
        self.connection.chart_prices[10] = [
            {"trade_date": date(2026, 6, 19), "close_price": Decimal("69000.00")},
            {"trade_date": date(2026, 6, 22), "close_price": Decimal("70000.00")},
            {"trade_date": date(2026, 6, 24), "close_price": Decimal("73500.00")},
        ]

        response = self.client.get("/api/journals/timeline/005930", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stock"]["stock_code"], "005930")
        self.assertEqual(len(body["series"]), 3)
        self.assertEqual(len(body["journals"]), 1)
        marker = body["journals"][0]
        self.assertEqual(marker["journal_id"], 20)
        # 작성일(6/22) 이하 최근 거래일 종가가 마커 기준점.
        self.assertEqual(marker["trade_date"], "2026-06-22")
        self.assertEqual(marker["price"], 70000.0)
        self.assertEqual(body["latest_price"], 73500.0)

    def test_journal_timeline_without_journals_returns_not_found(self):
        response = self.client.get("/api/journals/timeline/005930", headers=self.auth_headers())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "JOURNAL_NOT_FOUND")

    def test_journal_exposes_current_signal_comparison(self):
        self.create_journal()
        # 저장 후 신호가 갱신된 상황 — repo 가 api.signals_current 조인으로 채워주는 컬럼.
        self.connection.journals[0].update(
            {
                "current_signal_id": 322,
                "current_signal_score": Decimal("63.00"),
                "current_signal_value": "positive",
                "current_source_agreement": "HIGH",
            }
        )

        response = self.client.get("/api/journals/20", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        current = response.json()["current_signal"]
        self.assertEqual(current["final_signal_id"], 322)
        self.assertEqual(current["score"], 63.0)
        self.assertEqual(current["value"], "positive")

    def test_journal_without_published_signal_has_null_current_signal(self):
        self.create_journal()

        response = self.client.get("/api/journals/20", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["current_signal"])

    def test_journal_rejects_forbidden_user_view(self):
        response = self.create_journal(user_view="buy", memo="금지 값")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_USER_VIEW")

    def test_create_journal_rejects_unknown_stock(self):
        response = self.create_journal(stock_code="000000")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "STOCK_NOT_FOUND")

    def test_create_journal_rejects_unknown_signal(self):
        response = self.create_journal(final_signal_id=999)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_NOT_FOUND")

    def test_create_journal_rejects_signal_stock_mismatch(self):
        response = self.create_journal(final_signal_id=201)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "SIGNAL_STOCK_MISMATCH")

    def test_missing_journal_returns_not_found(self):
        cases = [
            ("get", "/api/journals/999", None),
            ("get", "/api/journals/999/chart", None),
            ("patch", "/api/journals/999", {"user_view": "watch"}),
            ("delete", "/api/journals/999", None),
        ]
        for method, url, body in cases:
            with self.subTest(method=method, url=url):
                kwargs = {"headers": self.auth_headers()}
                if body is not None:
                    kwargs["json"] = body
                response = getattr(self.client, method)(url, **kwargs)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["detail"]["code"], "JOURNAL_NOT_FOUND")

    def test_journal_is_isolated_between_users(self):
        self.create_journal()
        other_token = self.token_for(2, "other@example.com")

        response = self.client.get("/api/journals/20", headers=self.auth_headers(other_token))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "JOURNAL_NOT_FOUND")

    def test_create_journal_cleans_tags(self):
        raw_tags = [" DART ", "DART", ""] + [f"태그{i}" for i in range(1, 12)]

        response = self.create_journal(tags=raw_tags)

        self.assertEqual(response.status_code, 200)
        tags = response.json()["tags"]
        self.assertEqual(len(tags), 10)
        self.assertEqual(tags[0], "DART")
        self.assertEqual(len(tags), len(set(tags)))
        self.assertNotIn("", tags)


if __name__ == "__main__":
    unittest.main()
