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
    def __init__(self):
        self.users_by_email = {}
        self.users_by_id = {}
        self.sessions_by_hash = {}
        self.next_user_id = 1
        self.next_session_id = 1

    async def fetchrow(self, sql, *args):
        if "FROM users" in sql and "WHERE email = $1" in sql:
            return self.users_by_email.get(args[0])
        if "FROM users" in sql and "WHERE id = $1" in sql:
            return self.users_by_id.get(args[0])
        if "INSERT INTO users" in sql:
            user = {
                "id": self.next_user_id,
                "member_code": args[0],
                "email": args[1],
                "password_hash": args[2],
                "nickname": args[3],
                "agreed_risk": args[4],
                "is_verified": args[5],
                "email_verified_at": args[6],
            }
            self.next_user_id += 1
            self.users_by_email[user["email"]] = user
            self.users_by_id[user["id"]] = user
            return user
        if "INSERT INTO user_sessions" in sql:
            session = {
                "id": self.next_session_id,
                "user_id": args[0],
                "refresh_token_hash": args[1],
                "user_agent": args[2],
                "ip_address": args[3],
                "expires_at": args[4],
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
            json={
                "email": "USER@Example.com",
                "password": "password123",
                "nickname": "사용자",
                "agreed_risk": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user"]["email"], "user@example.com")
        self.assertEqual(body["user"]["nickname"], "사용자")
        self.assertTrue(body["user"]["agreed_risk"])
        self.assertEqual(body["token_type"], "bearer")
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertEqual(len(self.connection.sessions_by_hash), 1)
        session = next(iter(self.connection.sessions_by_hash.values()))
        self.assertIsNone(session["ip_address"])

    def test_signup_requires_risk_agreement(self):
        response = self.client.post(
            "/api/auth/signup",
            json={
                "email": "user@example.com",
                "password": "password123",
                "agreed_risk": False,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "RISK_AGREEMENT_REQUIRED")

    def test_login_refresh_logout_and_me_flow(self):
        signup = self.client.post(
            "/api/auth/signup",
            json={
                "email": "user@example.com",
                "password": "password123",
                "agreed_risk": True,
            },
        ).json()

        login_response = self.client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "password123"},
        )

        self.assertEqual(login_response.status_code, 200)
        login_body = login_response.json()
        me_response = self.client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {login_body['access_token']}"},
        )
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["email"], "user@example.com")

        refresh_response = self.client.post(
            "/api/auth/refresh",
            json={"refresh_token": login_body["refresh_token"]},
        )
        self.assertEqual(refresh_response.status_code, 200)
        self.assertIn("access_token", refresh_response.json())

        logout_response = self.client.post(
            "/api/auth/logout",
            json={"refresh_token": signup["refresh_token"]},
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(logout_response.json(), {"status": "ok"})

    def test_login_rejects_invalid_password(self):
        self.client.post(
            "/api/auth/signup",
            json={
                "email": "user@example.com",
                "password": "password123",
                "agreed_risk": True,
            },
        )

        response = self.client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_CREDENTIALS")


if __name__ == "__main__":
    unittest.main()
