import unittest
import warnings
from datetime import UTC, datetime

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.admin import get_database_pool
from app.core.portone import get_portone_client
from app.core.security import hash_password
from app.main import app


ADMIN_PASSWORD = "admin-pass-123"


class FakePortone:
    """real PortOne 대체: 관리자 환불의 결제 취소를 외부 호출 없이 모의한다."""

    def __init__(self):
        self.cancelled: list = []

    async def cancel_payment(self, payment_id, *, reason="user_cancel", amount=None):
        self.cancelled.append((payment_id, reason, amount))
        return {"paymentId": payment_id, "status": "CANCELLED", "amount": amount}


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
        self.payments = []  # portone_verifications(payment)

    async def fetchrow(self, sql, *args):
        if "INSERT INTO admin_audit_log" in sql:
            return {"id": 1}
        if "INSERT INTO payments" in sql:
            # record_payment / record_refund — append-only 이력
            return {"id": 1, "imp_uid": args[2], "amount": args[4]}
        if "FROM payments" in sql and "status = 'paid'" in sql:
            # get_latest_paid_payment
            paid = [p for p in self.payments if p.get("status") == "paid"]
            return paid[-1] if paid else None
        if "INSERT INTO portone_verifications" in sql:
            return {"id": 1, "imp_uid": args[1]}
        if "UPDATE signal_subscriptions" in sql:
            return {"id": 1, "status": "cancelled"}
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
        if "UPDATE users" in sql and "COALESCE" in sql:
            # update_user_profile: (user_id, nickname, email) — None 은 기존값 유지
            user_id, nickname, email = args[0], args[1], args[2]
            user = next((u for u in self.users if u["id"] == user_id), None)
            if user is None:
                return None
            if nickname is not None:
                user["nickname"] = nickname
            if email is not None:
                user["email"] = email
            return user
        if "AS total_users" in sql and "AS mrr" in sql:
            return {"total_users": len(self.users), "active_subscriptions": 1, "mrr": 9900}
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM portone_verifications" in sql:
            return list(self.payments)
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
        if "DELETE FROM users" in sql:
            # hard_delete_user — FK CASCADE/SET NULL 은 DB가 처리(여기선 users 행만 제거)
            existed = any(u["id"] == args[0] for u in self.users)
            self.users = [u for u in self.users if u["id"] != args[0]]
            return "DELETE 1" if existed else "DELETE 0"
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
        app.dependency_overrides[get_portone_client] = lambda: FakePortone()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def login(self):
        # 세션은 sa_admin HttpOnly 쿠키로 설정 → TestClient 쿠키 자가 이후 요청에 자동 송신.
        response = self.client.post(
            "/api/admin/login",
            json={"email": "admin@example.com", "password": ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        return {}

    def test_login_success_sets_admin_cookie(self):
        response = self.client.post(
            "/api/admin/login",
            json={"email": "admin@example.com", "password": ADMIN_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        # 세션 토큰은 body 가 아니라 sa_admin 쿠키로만 전달된다.
        self.assertNotIn("session_token", response.json())
        self.assertIsNotNone(response.cookies.get("sa_admin"))
        self.assertEqual(response.json()["admin"]["email"], "admin@example.com")
        self.assertEqual(self.connection.last_login_updated, [1])

    def test_admin_me_with_session(self):
        response = self.client.get("/api/admin/me", headers=self.login())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["admin"]["email"], "admin@example.com")

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

    def test_refund_user(self):
        headers = self.login()
        self.connection.payments = [
            {
                "id": 1,
                "imp_uid": "pay-x",
                "merchant_uid": "pay-x",
                "status": "paid",
                "user_id": 1,
                "amount": 9900,
                "subscription_id": None,
            }
        ]
        response = self.client.post("/api/admin/users/1/refund", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "refunded")
        self.assertEqual(response.json()["amount"], 9900)

    def test_refund_user_without_payment_returns_404(self):
        response = self.client.post("/api/admin/users/1/refund", headers=self.login())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "NO_REFUNDABLE_PAYMENT")

    def test_update_user(self):
        self.login()
        response = self.client.patch(
            "/api/admin/users/1", json={"nickname": "수정됨", "email": "edited@example.com"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["nickname"], "수정됨")
        self.assertEqual(response.json()["email"], "edited@example.com")
        self.assertEqual(self.connection.users[0]["nickname"], "수정됨")

    def test_update_user_not_found_returns_404(self):
        self.login()
        response = self.client.patch("/api/admin/users/999", json={"nickname": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "USER_NOT_FOUND")

    def test_update_user_nothing_returns_400(self):
        self.login()
        response = self.client.patch("/api/admin/users/1", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "NOTHING_TO_UPDATE")

    def test_delete_user(self):
        self.login()
        response = self.client.delete("/api/admin/users/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "deleted")
        self.assertEqual(len(self.connection.users), 0)

    def test_user_crud_requires_admin(self):
        self.assertEqual(
            self.client.patch("/api/admin/users/1", json={"nickname": "x"}).status_code, 401
        )
        self.assertEqual(self.client.delete("/api/admin/users/1").status_code, 401)


if __name__ == "__main__":
    unittest.main()
