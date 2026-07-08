import unittest
import warnings
from datetime import UTC, date, datetime, timedelta

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
        self.news_summary_row = {
            "total_articles": 42,
            "stock_count": 7,
            "latest_collected_at": datetime(2026, 7, 7, 9, 0, tzinfo=UTC),
            "recent_articles": 5,
        }
        self.recent_news_rows = [
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "title": "삼성전자 뉴스",
                "summary": "요약",
                "url": "https://n.example/1",
                "press": "언론사",
                "source": "NAVER_NEWS",
                "published_at": datetime(2026, 7, 7, 8, 0, tzinfo=UTC),
            },
            {
                "stock_code": "000660",
                "stock_name": None,  # 미매핑 종목 — 이름 없이 보존
                "title": "하이닉스 뉴스",
                "summary": None,
                "url": None,
                "press": None,
                "source": "NAVER_NEWS",
                "published_at": None,
            },
        ]
        # summary 집계에 전달된 recent_hours 를 기록해 클램프 검증에 사용.
        self.last_recent_hours = None
        # 종목별 일봉 종가 시리즈(stock_price_daily). 000660 은 미동기화(빈 시리즈).
        self.prices_by_stock = {
            10: [
                {"trade_date": date(2026, 7, 6), "close_price": 71000.0},
                {"trade_date": date(2026, 7, 7), "close_price": 71500.0},
            ],
        }

    async def fetchrow(self, sql, *args):
        if "FROM api.stock_news" in sql and "total_articles" in sql:
            self.last_recent_hours = args[0]
            return self.news_summary_row
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
        if "FROM api.stock_news" in sql and "LEFT JOIN api.stocks" in sql:
            return self.recent_news_rows[: args[0]]
        if "ticker ILIKE" in sql:
            query = args[0].strip("%")
            return [
                stock
                for stock in self.stocks_by_ticker.values()
                if query in stock["ticker"] or query in stock["name"]
            ][: args[1]]
        if "FROM api.stocks" in sql and "is_active = TRUE" in sql:
            return list(self.stocks_by_ticker.values())[: args[0]]
        if "FROM stock_price_daily" in sql:
            # args = (stock_id, from_date). 벽시계 비의존을 위해 날짜 필터는 생략.
            return list(self.prices_by_stock.get(args[0], []))
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

    def test_news_summary_returns_global_counts_publicly(self):
        # 공개 엔드포인트 — 토큰 없이 200, 전역 집계 필드 반환.
        response = self.client.get("/api/news/summary")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_articles"], 42)
        self.assertEqual(body["stock_count"], 7)
        self.assertEqual(body["recent_articles"], 5)
        self.assertTrue(body["latest_collected_at"].startswith("2026-07-07"))
        self.assertIn("notice", body)
        # 기본 윈도우 24h 가 집계에 전달되고 응답에 echo 된다.
        self.assertEqual(body["recent_hours"], 24)
        self.assertEqual(self.connection.last_recent_hours, 24)

    def test_news_summary_clamps_recent_hours(self):
        too_large = self.client.get("/api/news/summary?recent_hours=99999")
        self.assertEqual(too_large.status_code, 200)
        self.assertEqual(too_large.json()["recent_hours"], 720)
        self.assertEqual(self.connection.last_recent_hours, 720)

        too_small = self.client.get("/api/news/summary?recent_hours=0")
        self.assertEqual(too_small.json()["recent_hours"], 1)
        self.assertEqual(self.connection.last_recent_hours, 1)

    def test_recent_news_returns_global_feed_publicly(self):
        # 공개 엔드포인트 — 토큰 없이 200, 종목명 포함 전역 피드. 미매핑 종목은 name=null 보존.
        response = self.client.get("/api/news/recent?limit=10")

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(items[0]["stock_code"], "005930")
        self.assertEqual(items[0]["stock_name"], "삼성전자")
        self.assertEqual(items[0]["title"], "삼성전자 뉴스")
        self.assertIsNone(items[1]["stock_name"])
        self.assertIsNone(items[1]["published_at"])

    def test_stock_prices_returns_series_publicly(self):
        # 공개 엔드포인트 — 토큰 없이 200, 일봉 종가 시리즈 + 최신값.
        response = self.client.get("/api/stocks/005930/prices")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stock"]["stock_code"], "005930")
        self.assertEqual(body["stock"]["stock_name"], "삼성전자")
        self.assertEqual(len(body["series"]), 2)
        self.assertEqual(body["series"][0]["trade_date"], "2026-07-06")
        self.assertEqual(body["series"][-1]["close"], 71500.0)
        self.assertEqual(body["latest_price"], 71500.0)
        self.assertEqual(body["latest_trade_date"], "2026-07-07")

    def test_stock_prices_empty_series_when_unsynced(self):
        # 미동기화 종목 — 빈 시리즈 + null 최신값(프론트 "차트 준비 중").
        response = self.client.get("/api/stocks/000660/prices")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["series"], [])
        self.assertIsNone(body["latest_price"])
        self.assertIsNone(body["latest_trade_date"])

    def test_stock_prices_unknown_stock_returns_404(self):
        response = self.client.get("/api/stocks/999999/prices")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "STOCK_NOT_FOUND")

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
