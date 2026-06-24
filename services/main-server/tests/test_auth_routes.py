import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.auth import get_database_pool
from app.main import app


class FakeConnection:
    """포트원 본인인증 가입/로그인 흐름 SQL 모킹(dev 모드 portone 은 imp_uid→phone 결정적)."""

    def __init__(self):
        self.users_by_phone = {}
        self.users_by_email = {}
        self.users_by_id = {}
        self.sessions_by_hash = {}
        self.next_user_id = 1
        self.next_session_id = 1

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE phone = $1" in sql:
            return self.users_by_phone.get(args[0])
        if "FROM users" in sql and "WHERE email = $1" in sql:
            return self.users_by_email.get(args[0])
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "INSERT INTO users" in sql:
            # insert_identity_user: member_code, email, phone, nickname, agreed_risk
            user = {
                "id": self.next_user_id,
                "member_code": args[0],
                "email": args[1],
                "phone": args[2],
                "nickname": args[3],
                "agreed_risk": args[4],
                "is_verified": True,
            }
            self.next_user_id += 1
            self.users_by_phone[user["phone"]] = user
            self.users_by_email[user["email"]] = user
            self.users_by_id[user["id"]] = user
            return user
        if "INSERT INTO portone_verifications" in sql:
            return {"id": 1, "identity_verification_id": args[1]}
        if "INSERT INTO terms_agreements" in sql:
            return {"id": 1}
        if "FROM signal_subscriptions" in sql:
            return None  # 활성 구독 없음
        if "INSERT INTO user_sessions" in sql:
            session = {
                "id": self.next_session_id,
                "user_id": args[0],
                "refresh_token_hash": args[1],
                "expires_at": args[2],
                "user_agent": args[3],
                "ip_address": args[4],
                "revoked_at": None,
            }
            self.next_session_id += 1
            self.sessions_by_hash[session["refresh_token_hash"]] = session
            return session
        if "FROM user_sessions" in sql:
            session = self.sessions_by_hash.get(args[0])
            if session is None or session["revoked_at"] is not None:
                return None
            user = self.users_by_id[session["user_id"]]
            return {**session, **{f"user_{key}": value for key, value in user.items()}}
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def execute(self, sql, *args):
        if "UPDATE user_sessions" in sql:
            session = self.sessions_by_hash.get(args[0])
            if session:
                session["revoked_at"] = "now"
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


class AuthRoutesTest(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        app.dependency_overrides[get_database_pool] = lambda: FakePool(self.connection)
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_signup_creates_user_session_and_returns_tokens(self):
        response = self.client.post(
            "/api/auth/signup",
            json={"identity_verification_id": "imp_AAA", "email": "user@example.com", "nickname": "사용자", "agreed_risk": True},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        member_code = body["user"]["member_code"]
        self.assertEqual(len(member_code), 8)
        self.assertTrue(member_code[:4].isalpha() and member_code[4:].isdigit())
        self.assertEqual(body["user"]["nickname"], "사용자")
        self.assertTrue(body["user"]["agreed_risk"])
        self.assertFalse(body["user"]["subscription_active"])
        self.assertIsNotNone(body["user"]["phone_masked"])
        self.assertEqual(body["token_type"], "bearer")
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertEqual(len(self.connection.sessions_by_hash), 1)

    def test_signup_requires_risk_agreement(self):
        response = self.client.post(
            "/api/auth/signup",
            json={"identity_verification_id": "imp_AAA", "email": "user@example.com", "nickname": "사용자", "agreed_risk": False},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "RISK_AGREEMENT_REQUIRED")

    def test_signup_rejects_duplicate_phone(self):
        self.client.post("/api/auth/signup", json={"identity_verification_id": "imp_AAA", "email": "user@example.com", "nickname": "사용자", "agreed_risk": True})
        # 같은 imp_uid → 같은 phone → 중복 가입 차단
        response = self.client.post(
            "/api/auth/signup", json={"identity_verification_id": "imp_AAA", "email": "user@example.com", "nickname": "사용자", "agreed_risk": True}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "IDENTITY_ALREADY_REGISTERED")

    def test_login_refresh_logout_and_me_flow(self):
        signup = self.client.post(
            "/api/auth/signup", json={"identity_verification_id": "imp_AAA", "email": "user@example.com", "nickname": "사용자", "agreed_risk": True}
        ).json()
        member_code = signup["user"]["member_code"]

        login_response = self.client.post("/api/auth/login", json={"identity_verification_id": "imp_AAA"})
        self.assertEqual(login_response.status_code, 200)
        login_body = login_response.json()
        self.assertEqual(login_body["user"]["member_code"], member_code)

        me_response = self.client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {login_body['access_token']}"},
        )
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["member_code"], member_code)

        refresh_response = self.client.post(
            "/api/auth/refresh", json={"refresh_token": login_body["refresh_token"]}
        )
        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access_token", refresh_response.json())

        logout_response = self.client.post(
            "/api/auth/logout", json={"refresh_token": signup["refresh_token"]}
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(logout_response.json(), {"status": "ok"})

    def test_login_rejects_unknown_user(self):
        response = self.client.post("/api/auth/login", json={"identity_verification_id": "imp_NEVER"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "USER_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
