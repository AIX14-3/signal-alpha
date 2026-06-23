import unittest
import warnings
from datetime import UTC, datetime, timedelta

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.subscriptions import get_database_pool
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app


def _plan(plan_id, plan_type, name, max_watchlist, price_monthly, is_active=True):
    return {
        "id": plan_id,
        "plan_type": plan_type,
        "plan_display_name": name,
        "max_watchlist": max_watchlist,
        "signal_delay_hours": 24,
        "journal_max_entries": 50,
        "has_alt_data": plan_type != "free",
        "has_detail_report": plan_type != "free",
        "has_backtesting": plan_type == "premium",
        "price_monthly": price_monthly,
        "price_yearly": price_monthly * 10,
        "is_active": is_active,
    }


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
        self.plans = {
            "free": _plan(1, "free", "Free", 3, 0),
            "pro": _plan(2, "pro", "Pro", 20, 9900),
            "premium": _plan(3, "premium", "Premium", 100, 19900),
        }
        self.active_subscription = None  # dict 또는 None
        self.created = []

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM signal_subscriptions" in sql and "INNER JOIN subscription_plans" in sql:
            return self.active_subscription
        if "UPDATE signal_subscriptions" in sql and "status = 'cancelled'" in sql:
            cancelled = self.active_subscription
            self.active_subscription = None
            return cancelled
        if "INSERT INTO signal_subscriptions" in sql:
            user_id, plan_id, status, expires_at, payment_method, billing_cycle = args
            plan = next(p for p in self.plans.values() if p["id"] == plan_id)
            row = {
                "id": 99,
                "user_id": user_id,
                "plan_id": plan_id,
                "status": status,
                "started_at": datetime(2026, 6, 23, tzinfo=UTC),
                "expires_at": expires_at,
                "billing_cycle": billing_cycle,
                "plan_type": plan["plan_type"],
            }
            self.active_subscription = {**row, **plan}
            self.created.append(row)
            return row
        if "FROM subscription_plans" in sql and "WHERE plan_type = $1" in sql:
            return self.plans.get(args[0])
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM subscription_plans" in sql and "ORDER BY price_monthly" in sql:
            return [p for p in self.plans.values() if p["is_active"]]
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


class SubscriptionRoutesTest(unittest.TestCase):
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

    def test_list_plans_is_public(self):
        response = self.client.get("/api/subscriptions/plans")
        self.assertEqual(response.status_code, 200)
        types = [plan["plan_type"] for plan in response.json()["plans"]]
        self.assertEqual(types, ["free", "pro", "premium"])
        self.assertNotIn("is_active", response.json()["plans"][0])

    def test_me_requires_authentication(self):
        response = self.client.get("/api/subscriptions/me")
        self.assertEqual(response.status_code, 401)

    def test_me_without_subscription_returns_free(self):
        response = self.client.get("/api/subscriptions/me", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["subscription"])
        self.assertEqual(response.json()["plan"]["plan_type"], "free")

    def test_subscribe_to_pro_creates_active_subscription(self):
        response = self.client.post(
            "/api/subscriptions",
            json={"plan_type": "pro", "billing_cycle": "monthly"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan"]["plan_type"], "pro")
        self.assertEqual(response.json()["subscription"]["status"], "active")
        self.assertEqual(response.json()["subscription"]["billing_cycle"], "monthly")
        self.assertEqual(len(self.connection.created), 1)

    def test_cancel_clears_subscription(self):
        self.connection.active_subscription = {
            "id": 5,
            "user_id": 1,
            "plan_id": 2,
            "status": "active",
            "started_at": datetime(2026, 6, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 7, 1, tzinfo=UTC),
            "billing_cycle": "monthly",
            **self.connection.plans["pro"],
        }
        response = self.client.post(
            "/api/subscriptions",
            json={"plan_type": "pro", "action": "cancel"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["subscription"])
        self.assertEqual(response.json()["plan"]["plan_type"], "free")
        self.assertIsNone(self.connection.active_subscription)


if __name__ == "__main__":
    unittest.main()
