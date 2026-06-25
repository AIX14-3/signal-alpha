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
from app.core.portone import PortOneError, get_portone_client
from app.core.security import create_access_token
from app.main import app


def _plan(plan_id, plan_type, name, max_watchlist, price_monthly, is_active=True):
    return {
        "id": plan_id,
        "plan_type": plan_type,
        "plan_display_name": name,
        "max_watchlist": max_watchlist,
        "signal_delay_hours": 0 if plan_type != "free" else 24,
        "journal_max_entries": 50,
        "has_alt_data": plan_type != "free",
        "has_detail_report": plan_type != "free",
        "has_backtesting": False,
        "price_monthly": price_monthly,
        "price_yearly": 0,
        "is_active": is_active,
    }


class FakeConnection:
    def __init__(self):
        self.users_by_id = {
            1: {"id": 1, "member_code": "ABCD1234", "nickname": "사용자", "agreed_risk": True}
        }
        # 신규 모델: free + monthly_9900 활성, 구 pro/premium 비활성
        self.plans = {
            "free": _plan(1, "free", "Free", 2147483647, 0),
            "monthly_9900": _plan(2, "monthly_9900", "월 구독", 2147483647, 9900),
            "pro": _plan(3, "pro", "Pro", 20, 9900, is_active=False),
            "premium": _plan(4, "premium", "Premium", 100, 19900, is_active=False),
        }
        self.active_subscription = None
        # 최근 결제 검증 레코드(없으면 None — 관리자 부여 구독을 모사).
        self.latest_payment = None
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
        if "FROM portone_verifications" in sql:
            return self.latest_payment
        if "INSERT INTO portone_verifications" in sql:
            return {"id": 1}
        if "INSERT INTO signal_subscriptions" in sql:
            user_id, plan_id, status, expires_at, payment_method, billing_cycle = args
            plan = next(p for p in self.plans.values() if p["id"] == plan_id)
            row = {
                "id": 99,
                "user_id": user_id,
                "plan_id": plan_id,
                "status": status,
                "started_at": datetime(2026, 6, 24, tzinfo=UTC),
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


class FakePortOne:
    """real 모드(dev_mode=False) 포트원 클라이언트 대역.

    실제 키 없이 cancel_payment 호출/실패 경로를 검증한다.
    """

    def __init__(self, *, dev_mode=False, raise_error=False):
        self.dev_mode = dev_mode
        self.raise_error = raise_error
        self.calls = []

    async def cancel_payment(self, payment_id, *, reason="user_cancel"):
        self.calls.append((payment_id, reason))
        if self.raise_error:
            raise PortOneError("cancel failed")
        return {"paymentId": payment_id, "status": "CANCELLED"}


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
        self.assertEqual(types, ["free", "monthly_9900"])
        self.assertNotIn("is_active", response.json()["plans"][0])

    def test_me_requires_authentication(self):
        response = self.client.get("/api/subscriptions/me")
        self.assertEqual(response.status_code, 401)

    def test_me_without_subscription_returns_free(self):
        response = self.client.get("/api/subscriptions/me", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["subscription"])
        self.assertEqual(response.json()["plan"]["plan_type"], "free")

    def test_payment_confirm_creates_subscription(self):
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        self.assertEqual(checkout.status_code, 200)
        self.assertEqual(checkout.json()["amount"], 9900)
        payment_id = checkout.json()["payment_id"]

        confirm = self.client.post(
            "/api/payments/confirm",
            json={"payment_id": payment_id},
            headers=self.auth_headers(),
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.assertEqual(confirm.json()["subscription"]["plan_type"], "monthly_9900")
        self.assertEqual(confirm.json()["subscription"]["status"], "active")
        self.assertEqual(len(self.connection.created), 1)

    def _seed_active_subscription(self):
        self.connection.active_subscription = {
            "id": 5,
            "user_id": 1,
            "plan_id": 2,
            "status": "active",
            "started_at": datetime(2026, 6, 1, tzinfo=UTC),
            "expires_at": datetime(2026, 7, 1, tzinfo=UTC),
            "billing_cycle": "monthly",
            **self.connection.plans["monthly_9900"],
        }

    def test_payment_cancel_clears_subscription(self):
        # dev 모드(기본): 포트원 외부 호출 없이 구독만 취소된다.
        self._seed_active_subscription()
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertIsNone(self.connection.active_subscription)

    def test_payment_cancel_without_active_returns_404(self):
        # 활성 구독이 없으면 404 SUBSCRIPTION_NOT_FOUND.
        self.assertIsNone(self.connection.active_subscription)
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SUBSCRIPTION_NOT_FOUND")

    def test_payment_cancel_admin_granted_without_payment_record(self):
        # 관리자 부여 구독(결제 레코드 없음): real 모드라도 포트원 호출 없이 취소 성공.
        self._seed_active_subscription()
        self.connection.latest_payment = None
        portone = FakePortOne(dev_mode=False)
        app.dependency_overrides[get_portone_client] = lambda: portone
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertIsNone(self.connection.active_subscription)
        self.assertEqual(portone.calls, [])  # 결제 레코드 없음 → cancel_payment 미호출

    def test_payment_cancel_real_mode_calls_portone(self):
        # real 모드 + 결제 레코드 존재: 포트원 cancel_payment 가 호출되고 구독 취소.
        self._seed_active_subscription()
        self.connection.latest_payment = {"imp_uid": "sa-pay-abc123"}
        portone = FakePortOne(dev_mode=False)
        app.dependency_overrides[get_portone_client] = lambda: portone
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(portone.calls, [("sa-pay-abc123", "user_cancel")])
        self.assertIsNone(self.connection.active_subscription)

    def test_payment_cancel_portone_error_still_cancels(self):
        # 포트원 취소가 실패해도 구독은 취소되고 200 (실패는 로깅으로 표면화).
        self._seed_active_subscription()
        self.connection.latest_payment = {"imp_uid": "sa-pay-err"}
        portone = FakePortOne(dev_mode=False, raise_error=True)
        app.dependency_overrides[get_portone_client] = lambda: portone
        with self.assertLogs("app.api.routes.payments", level="WARNING") as logs:
            response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertIsNone(self.connection.active_subscription)
        self.assertTrue(any("PortOne cancel failed" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
