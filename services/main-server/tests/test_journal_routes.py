import unittest
import warnings
from datetime import UTC, datetime, timedelta
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
            }
        }
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
        self.journals = []
        self.next_journal_id = 20

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM stocks" in sql and "WHERE ticker = $1" in sql:
            return self.stocks_by_ticker.get(args[0])
        if "final_signals.id = $1" in sql:
            return self.signal if args[0] == self.signal["id"] else None
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
            journal["updated_at"] = datetime(2026, 6, 23, tzinfo=UTC)
            return journal
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
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

    def test_journals_require_authentication(self):
        response = self.client.get("/api/journals")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_create_list_detail_update_and_delete_journal(self):
        create_response = self.client.post(
            "/api/journals",
            json={
                "stock_code": "005930",
                "final_signal_id": 200,
                "user_view": "research_more",
                "memo": "Report 데이터가 없어 추가 근거 확인이 필요합니다.",
                "tags": ["DART", "추가확인"],
            },
            headers=self.auth_headers(),
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()
        self.assertEqual(created["journal_id"], 20)
        self.assertEqual(created["stock_code"], "005930")
        self.assertEqual(created["final_signal_id"], 200)
        self.assertEqual(created["user_view"], "research_more")
        self.assertEqual(created["tags"], ["DART", "추가확인"])
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

    def test_journal_rejects_forbidden_user_view(self):
        response = self.client.post(
            "/api/journals",
            json={
                "stock_code": "005930",
                "final_signal_id": 200,
                "user_view": "buy",
                "memo": "금지 값",
            },
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_USER_VIEW")


if __name__ == "__main__":
    unittest.main()
