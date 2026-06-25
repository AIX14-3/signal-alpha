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
from app.core.portone import PaymentResult, get_portone_client
from app.core.security import create_access_token
from app.main import app


class _FakeYearlyPortone:
    dev_mode = True

    async def verify_payment(self, payment_id: str) -> PaymentResult:
        return PaymentResult(payment_id=payment_id, amount=99000, status="paid", raw={})


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
        self.created = []
        self.verifications = {}  # imp_uid -> record

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "FROM portone_verifications" in sql and "WHERE imp_uid = $1" in sql:
            return self.verifications.get(args[0])
        if "FROM signal_subscriptions" in sql and "INNER JOIN subscription_plans" in sql:
            return self.active_subscription
        if "UPDATE signal_subscriptions" in sql and "status = 'cancelled'" in sql:
            cancelled = self.active_subscription
            self.active_subscription = None
            return cancelled
        if "UPDATE signal_subscriptions" in sql and "cancelled_at = NULL" in sql:
            # resume_subscription: 해지 예약(cancelled_at) 철회.
            if self.active_subscription is not None and self.active_subscription.get("cancelled_at"):
                self.active_subscription = {**self.active_subscription, "cancelled_at": None}
                return self.active_subscription
            return None
        if "UPDATE signal_subscriptions" in sql:
            # schedule_subscription_cancel: status 유지 + cancelled_at 기록(만료일까지 접근권 유지).
            if self.active_subscription is not None:
                self.active_subscription = {
                    **self.active_subscription,
                    "cancelled_at": "2026-06-25T00:00:00+00:00",
                }
            return self.active_subscription
        if "INSERT INTO portone_verifications" in sql:
            record = {
                "id": len(self.verifications) + 1,
                "user_id": args[0],
                "imp_uid": args[1],
                "merchant_uid": args[2],
                "verification_type": args[3],
                "status": args[4],
                "verified_at": args[5],
                "created_at": datetime(2026, 6, 25, tzinfo=UTC),
            }
            self.verifications[args[1]] = record  # upsert(imp_uid 유니크)
            return record
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
        if "FROM portone_verifications" in sql:
            return [
                v
                for v in self.verifications.values()
                if v["verification_type"] == "payment" and v["status"] == "paid"
            ]
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

    def _seed_active(self, expires):
        self.connection.active_subscription = {
            "id": 5,
            "user_id": 1,
            "plan_id": 2,
            "status": "active",
            "started_at": datetime(2026, 6, 1, tzinfo=UTC),
            "expires_at": expires,
            "billing_cycle": "monthly",
            **self.connection.plans["monthly_9900"],
        }

    def test_payment_cancel_schedules_cancellation(self):
        # 갱신 중지형: 즉시 박탈/환불이 아니라 cancelled_at 만 기록, 만료일까지 접근권 유지.
        self._seed_active(datetime(2026, 7, 1, tzinfo=UTC))
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancel_scheduled")
        self.assertIsNotNone(self.connection.active_subscription)
        self.assertIsNotNone(self.connection.active_subscription["cancelled_at"])

    def test_payment_cancel_without_active_returns_404(self):
        response = self.client.post("/api/payments/cancel", headers=self.auth_headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "SUBSCRIPTION_NOT_FOUND")

    def test_checkout_blocked_when_already_subscribed(self):
        # 과금 전 차단: 활성 구독이면 checkout 이 409 → paymentId 미발급.
        self._seed_active(datetime(2030, 1, 1, tzinfo=UTC))
        response = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "ALREADY_SUBSCRIBED")

    def test_confirm_extends_active_subscription(self):
        # 안전망: 활성 상태에서 결제가 확정되면 거절(금전 손실)이 아니라 만료일부터 30일 연장.
        self._seed_active(datetime(2030, 1, 1, tzinfo=UTC))
        confirm = self.client.post(
            "/api/payments/confirm",
            json={"payment_id": "sa-pay-extend"},
            headers=self.auth_headers(),
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.assertEqual(
            self.connection.created[-1]["expires_at"], datetime(2030, 1, 31, tzinfo=UTC)
        )

    def test_confirm_is_idempotent(self):
        # 동일 paymentId 로 두 번 confirm → 구독은 한 번만 생성(이중 연장 방지).
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        payment_id = checkout.json()["payment_id"]
        first = self.client.post(
            "/api/payments/confirm", json={"payment_id": payment_id}, headers=self.auth_headers()
        )
        second = self.client.post(
            "/api/payments/confirm", json={"payment_id": payment_id}, headers=self.auth_headers()
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self.connection.created), 1)

    def test_resume_clears_cancellation(self):
        self._seed_active(datetime(2026, 7, 1, tzinfo=UTC))
        self.connection.active_subscription["cancelled_at"] = "2026-06-25T00:00:00+00:00"
        response = self.client.post("/api/payments/resume", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")
        self.assertIsNone(self.connection.active_subscription["cancelled_at"])

    def test_resume_without_cancellation_returns_404(self):
        self._seed_active(datetime(2026, 7, 1, tzinfo=UTC))  # cancelled_at 없음
        response = self.client.post("/api/payments/resume", headers=self.auth_headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "NO_CANCEL_SCHEDULED")

    def test_history_lists_paid_payments(self):
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        payment_id = checkout.json()["payment_id"]
        self.client.post(
            "/api/payments/confirm", json={"payment_id": payment_id}, headers=self.auth_headers()
        )
        response = self.client.get("/api/payments/history", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["payment_id"], payment_id)
        self.assertEqual(items[0]["amount"], 9900)

    def test_webhook_grants_subscription(self):
        # checkout 으로 paymentId↔user 매핑 적재 후 webhook 수신 → 구독 생성(서명 없음=dev).
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        payment_id = checkout.json()["payment_id"]
        response = self.client.post(
            "/api/payments/webhook",
            json={"type": "Transaction.Paid", "data": {"paymentId": payment_id}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(len(self.connection.created), 1)

    def test_webhook_idempotent_after_confirm(self):
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        payment_id = checkout.json()["payment_id"]
        self.client.post(
            "/api/payments/confirm", json={"payment_id": payment_id}, headers=self.auth_headers()
        )
        self.client.post(
            "/api/payments/webhook", json={"data": {"paymentId": payment_id}}
        )
        self.assertEqual(len(self.connection.created), 1)

    def test_webhook_ignores_unknown_payment(self):
        response = self.client.post(
            "/api/payments/webhook", json={"data": {"paymentId": "sa-pay-unknown"}}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")

    def _seed_paid(self, imp_uid, paid_at):
        self.connection.verifications[imp_uid] = {
            "id": 1,
            "user_id": 1,
            "imp_uid": imp_uid,
            "merchant_uid": imp_uid,
            "verification_type": "payment",
            "status": "paid",
            "verified_at": paid_at,
            "created_at": paid_at,
        }

    def test_me_includes_expiring_soon(self):
        self._seed_active(datetime.now(UTC) + timedelta(days=3))
        response = self.client.get("/api/subscriptions/me", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        sub = response.json()["subscription"]
        self.assertTrue(sub["expiring_soon"])
        self.assertLessEqual(sub["days_remaining"], 3)

    def test_receipt_returns_payment(self):
        checkout = self.client.post("/api/payments/checkout", headers=self.auth_headers())
        payment_id = checkout.json()["payment_id"]
        self.client.post(
            "/api/payments/confirm", json={"payment_id": payment_id}, headers=self.auth_headers()
        )
        response = self.client.get(f"/api/payments/{payment_id}/receipt", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payment_id"], payment_id)
        self.assertEqual(response.json()["amount"], 9900)

    def test_refund_full_within_window(self):
        now = datetime.now(UTC)
        self._seed_active(now + timedelta(days=25))
        self.connection.active_subscription["started_at"] = now - timedelta(days=5)
        self._seed_paid("pay-full", now)  # 결제 직후 → 7일 이내 전액
        response = self.client.post("/api/payments/refund", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "full")
        self.assertEqual(response.json()["amount"], 9900)
        self.assertIsNone(self.connection.active_subscription)  # 즉시 해지

    def test_refund_prorated_after_window(self):
        now = datetime.now(UTC)
        # 경계 floor 오차 방지를 위해 시간 버퍼를 둔다(총 30일, 잔여 10일).
        self._seed_active(now + timedelta(days=10, hours=1))
        self.connection.active_subscription["started_at"] = now - timedelta(days=20, hours=1)
        self._seed_paid("pay-pro", now - timedelta(days=20))  # 7일 초과 → 일할
        response = self.client.post("/api/payments/refund", headers=self.auth_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["kind"], "prorated")
        self.assertEqual(response.json()["amount"], 3300)  # 9900 * 10/30

    def test_refund_without_payment_returns_409(self):
        self._seed_active(datetime.now(UTC) + timedelta(days=10))
        response = self.client.post("/api/payments/refund", headers=self.auth_headers())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "NO_REFUNDABLE_PAYMENT")

    def test_checkout_yearly_returns_yearly_amount(self):
        response = self.client.post(
            "/api/payments/checkout", json={"cycle": "yearly"}, headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["amount"], 99000)
        self.assertIn("연간", response.json()["order_name"])

    def test_confirm_yearly_grants_yearly_cycle(self):
        app.dependency_overrides[get_portone_client] = lambda: _FakeYearlyPortone()
        try:
            checkout = self.client.post(
                "/api/payments/checkout", json={"cycle": "yearly"}, headers=self.auth_headers()
            )
            payment_id = checkout.json()["payment_id"]
            confirm = self.client.post(
                "/api/payments/confirm",
                json={"payment_id": payment_id},
                headers=self.auth_headers(),
            )
            self.assertEqual(confirm.status_code, 200, confirm.text)
            self.assertEqual(self.connection.created[-1]["billing_cycle"], "yearly")
        finally:
            del app.dependency_overrides[get_portone_client]


if __name__ == "__main__":
    unittest.main()
