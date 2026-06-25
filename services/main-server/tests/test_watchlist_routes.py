import unittest
import warnings
from datetime import UTC, datetime, timedelta

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.watchlists import get_database_pool
from app.core.config import get_settings
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
            },
            "000660": {
                "id": 11,
                "ticker": "000660",
                "name": "SK하이닉스",
                "market": "KOSPI",
                "sector": "반도체",
                "is_active": True,
            },
        }
        self.watchlists = []
        self.next_watchlist_id = 1

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM api.stocks" in sql and "WHERE ticker = $1" in sql:
            return self.stocks_by_ticker.get(args[0])
        if "FROM watchlists" in sql and "INNER JOIN api.stocks" in sql:
            watchlist = next(
                (
                    row
                    for row in self.watchlists
                    if row["user_id"] == args[0] and row["stock_id"] == args[1]
                ),
                None,
            )
            if watchlist is None:
                return None
            stock = next(
                stock
                for stock in self.stocks_by_ticker.values()
                if stock["id"] == watchlist["stock_id"]
            )
            return {**watchlist, **stock}
        if "INSERT INTO watchlists" in sql:
            existing = next(
                (
                    row
                    for row in self.watchlists
                    if row["user_id"] == args[0] and row["stock_id"] == args[1]
                ),
                None,
            )
            if existing is not None:
                existing["notification_enabled"] = args[2]
                return existing
            row = {
                "id": self.next_watchlist_id,
                "user_id": args[0],
                "stock_id": args[1],
                "notification_enabled": args[2],
                "created_at": datetime(2026, 6, 18, tzinfo=UTC),
            }
            self.next_watchlist_id += 1
            self.watchlists.append(row)
            return row
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "ticker ILIKE" in sql:
            query = args[0].strip("%")
            return [
                stock
                for stock in self.stocks_by_ticker.values()
                if query in stock["ticker"] or query in stock["name"]
            ][: args[1]]
        if "FROM api.stocks" in sql and "is_active = TRUE" in sql:
            return list(self.stocks_by_ticker.values())[: args[0]]
        if "FROM watchlists" in sql:
            rows = []
            for watchlist in self.watchlists:
                if watchlist["user_id"] != args[0]:
                    continue
                stock = next(
                    stock
                    for stock in self.stocks_by_ticker.values()
                    if stock["id"] == watchlist["stock_id"]
                )
                rows.append({**watchlist, **stock})
            return rows
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql:
            return sum(1 for row in self.watchlists if row["user_id"] == args[0])
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")

    async def execute(self, sql, *args):
        if "DELETE FROM watchlists" in sql:
            self.watchlists = [
                row
                for row in self.watchlists
                if not (row["user_id"] == args[0] and row["stock_id"] == args[1])
            ]
            return "DELETE 1"
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


class WatchlistRoutesTest(unittest.TestCase):
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

    def test_stock_search_returns_matching_active_stocks(self):
        response = self.client.get("/api/stocks/search?query=삼성")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["stock_code"], "005930")
        self.assertEqual(response.json()["items"][0]["stock_name"], "삼성전자")

    def test_list_stocks_returns_active_stocks(self):
        response = self.client.get("/api/stocks")

        self.assertEqual(response.status_code, 200)
        codes = {item["stock_code"] for item in response.json()["items"]}
        self.assertEqual(codes, {"005930", "000660"})

    def test_watchlist_requires_authentication(self):
        response = self.client.get("/api/watchlists")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "AUTH_REQUIRED")

    def test_add_list_and_delete_watchlist(self):
        add_response = self.client.post(
            "/api/watchlists",
            json={"stock_code": "005930"},
            headers=self.auth_headers(),
        )

        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(add_response.json()["stock"]["stock_code"], "005930")
        self.assertFalse(add_response.json()["notification_enabled"])

        list_response = self.client.get("/api/watchlists", headers=self.auth_headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertNotIn("limit", list_response.json())
        self.assertEqual(list_response.json()["count"], 1)

        delete_response = self.client.delete(
            "/api/watchlists/005930",
            headers=self.auth_headers(),
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json(), {"status": "deleted"})

    def test_add_watchlist_rejects_unknown_stock(self):
        response = self.client.post(
            "/api/watchlists",
            json={"stock_code": "999999"},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "STOCK_NOT_FOUND")

    def test_add_existing_watchlist_is_idempotent_even_at_limit(self):
        self.connection.watchlists.append(
            {
                "id": 1,
                "user_id": 1,
                "stock_id": 10,
                "notification_enabled": False,
                "created_at": datetime(2026, 6, 18, tzinfo=UTC),
            }
        )
        for index in range(9):
            self.connection.watchlists.append(
                {
                    "id": index + 2,
                    "user_id": 1,
                    "stock_id": 100 + index,
                    "notification_enabled": False,
                    "created_at": datetime(2026, 6, 18, tzinfo=UTC),
                }
            )

        response = self.client.post(
            "/api/watchlists",
            json={"stock_code": "005930"},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stock"]["stock_code"], "005930")
        self.assertEqual(len(self.connection.watchlists), 10)

    def test_add_watchlist_is_unlimited(self):
        # 신규 기획: 관심종목 무제한 — 10개를 넘겨도 추가된다.
        for index in range(10):
            self.connection.watchlists.append(
                {
                    "id": index + 1,
                    "user_id": 1,
                    "stock_id": 100 + index,
                    "notification_enabled": False,
                    "created_at": datetime(2026, 6, 18, tzinfo=UTC),
                }
            )

        response = self.client.post(
            "/api/watchlists",
            json={"stock_code": "005930"},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stock"]["stock_code"], "005930")
        self.assertEqual(len(self.connection.watchlists), 11)


if __name__ == "__main__":
    unittest.main()
