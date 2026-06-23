import unittest
import warnings
from datetime import UTC, datetime, timedelta

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.admin import get_database_pool
from app.core.security import hash_password
from app.main import app


ADMIN_PASSWORD = "admin-pass-123"


class FakeConnection:
    def __init__(self):
        self.admins_by_email = {
            "admin@example.com": {
                "id": 1,
                "email": "admin@example.com",
                "password_hash": hash_password(ADMIN_PASSWORD),
                "is_active": True,
            }
        }
        self.sessions = {}  # token -> {expires_at, admin_id, admin_email}
        self.users = [
            {
                "id": 1,
                "member_code": "U0001",
                "email": "user@example.com",
                "nickname": "사용자",
                "created_at": datetime(2026, 6, 1, tzinfo=UTC),
                "plan_type": "pro",
                "subscription_status": "active",
            }
        ]
        self.last_login_updated = []

    async def fetchrow(self, sql, *args):
        if "FROM admin_accounts" in sql and "WHERE email = $1" in sql:
            return self.admins_by_email.get(args[0])
        if "INSERT INTO admin_sessions" in sql:
            admin_id, session_token, expires_at = args[0], args[1], args[2]
            self.sessions[session_token] = {
                "expires_at": expires_at,
                "admin_id": admin_id,
                "admin_email": "admin@example.com",
            }
            return {"id": 1, "session_token": session_token, "expires_at": expires_at}
        if "FROM admin_sessions" in sql and "INNER JOIN admin_accounts" in sql:
            session = self.sessions.get(args[0])
            if session is None or session["expires_at"] <= datetime.now(UTC):
                return None
            return {
                "session_id": 1,
                "session_token": args[0],
                "expires_at": session["expires_at"],
                "admin_id": session["admin_id"],
                "admin_email": session["admin_email"],
            }
        if "FROM users" in sql and "WHERE users.id = $1" in sql:
            user = next((u for u in self.users if u["id"] == args[0]), None)
            if user is None:
                return None
            return {**user, "agreed_risk": True, "is_verified": False, "watchlist_count": 2}
        if "AS total_users" in sql and "AS mrr" in sql:
            return {"total_users": len(self.users), "active_subscriptions": 1, "mrr": 9900}
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM users" in sql and "LIMIT $1 OFFSET $2" in sql:
            return list(self.users)
        if "FROM subscription_plans" in sql and "LEFT JOIN signal_subscriptions" in sql:
            return [
                {"plan_type": "free", "count": 0},
                {"plan_type": "pro", "count": 1},
                {"plan_type": "premium", "count": 0},
            ]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql and "FROM users" in sql:
            return len(self.users)
        raise AssertionError(f"Unexpected fetchval SQL: {sql}")

    async def execute(self, sql, *args):
        if "UPDATE admin_accounts" in sql and "last_login_at" in sql:
            self.last_login_updated.append(args[0])
            return "UPDATE 1"
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


class AdminRoutesTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(self.connection)
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def login(self):
        response = self.client.post(
            "/api/admin/login",
            json={"email": "admin@example.com", "password": ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['session_token']}"}

    def test_login_success_returns_session_token(self):
        response = self.client.post(
            "/api/admin/login",
            json={"email": "admin@example.com", "password": ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("session_token", response.json())
        self.assertEqual(self.connection.last_login_updated, [1])

    def test_login_rejects_wrong_password(self):
        response = self.client.post(
            "/api/admin/login",
            json={"email": "admin@example.com", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_CREDENTIALS")

    def test_list_users_requires_admin(self):
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "ADMIN_AUTH_REQUIRED")

    def test_list_users_with_session(self):
        response = self.client.get("/api/admin/users", headers=self.login())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["subscription"]["plan_type"], "pro")

    def test_stats_with_session(self):
        response = self.client.get("/api/admin/stats", headers=self.login())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mrr"], 9900)
        self.assertEqual(response.json()["by_plan"]["pro"], 1)
        self.assertEqual(response.json()["active_subscriptions"], 1)


if __name__ == "__main__":
    unittest.main()
