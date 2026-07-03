import unittest
import warnings
from datetime import UTC, datetime

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.core.database import get_database_pool
from app.core.security import hash_password
from app.main import app


ADMIN_PASSWORD = "admin-pass-123"


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


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
        # 마이그레이션이 시드하는 싱글턴 1행과 동일한 기본값.
        self.guard_status = {
            "id": 1,
            "status": "ok",
            "scope": "report_generation",
            "mode": "advisory",
            "reason": None,
            "resume_at": None,
            "triggered_by": None,
            "updated_at": datetime(2026, 7, 3, tzinfo=UTC),
        }
        self.recommendations = []
        self.audit = []

    def transaction(self):
        return FakeTransaction()

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
        if "UPDATE guard_site_status" in sql and "status = 'blocked'" in sql:
            # 제안 승인 경로 — scope/reason/actor 만 받아 blocked 로 전환.
            self.guard_status.update(
                {
                    "status": "blocked",
                    "scope": args[0],
                    "reason": args[1],
                    "triggered_by": args[2],
                    "updated_at": datetime.now(UTC),
                }
            )
            return dict(self.guard_status)
        if "UPDATE guard_site_status" in sql:
            self.guard_status.update(
                {
                    "status": args[0],
                    "scope": args[1],
                    "mode": args[2],
                    "reason": args[3],
                    "resume_at": args[4],
                    "triggered_by": args[5],
                    "updated_at": datetime.now(UTC),
                }
            )
            return dict(self.guard_status)
        if "FROM guard_site_status" in sql:
            return dict(self.guard_status)
        if "UPDATE guard_recommendations" in sql and "status = 'pending'" in sql:
            recommendation_id, decision, actor = args[0], args[1], args[2]
            for item in self.recommendations:
                if item["id"] == recommendation_id and item["status"] == "pending":
                    item.update(
                        {
                            "status": decision,
                            "decided_by": actor,
                            "decided_at": datetime.now(UTC),
                        }
                    )
                    return dict(item)
            return None
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetch(self, sql, *args):
        if "FROM guard_status_audit" in sql:
            return list(reversed(self.audit))[: args[0]]
        if "FROM guard_recommendations" in sql:
            status, limit = args[0], args[1]
            rows = [
                {**item, "news_title": None, "news_url": None}
                for item in self.recommendations
                if item["status"] == status
            ]
            return rows[:limit]
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def execute(self, sql, *args):
        if "UPDATE admin_accounts" in sql and "last_login_at" in sql:
            return "UPDATE 1"
        if "INSERT INTO guard_status_audit" in sql:
            self.audit.append(
                {
                    "action": args[0],
                    "scope": args[1],
                    "reason": args[2],
                    "actor": args[3],
                    "created_at": datetime.now(UTC),
                }
            )
            return "INSERT 0 1"
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


class GuardRoutesTest(unittest.TestCase):
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

    def seed_recommendation(self, recommendation_id=1, status="pending"):
        self.connection.recommendations.append(
            {
                "id": recommendation_id,
                "news_event_id": None,
                "suggested_scope": "report_view",
                "severity": 82,
                "reason": "이란-미국 분쟁 확전 속보",
                "status": status,
                "decided_by": None,
                "decided_at": None,
                "created_at": datetime(2026, 7, 3, tzinfo=UTC),
            }
        )

    # ── 공개 status ──────────────────────────────────────────────

    def test_public_status_default_ok(self):
        response = self.client.get("/api/guard/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["scope"], "report_generation")
        self.assertIsNone(body["reason"])

    def test_public_status_reflects_block(self):
        self.connection.guard_status.update(
            {"status": "blocked", "scope": "whole_site", "reason": "전쟁 속보"}
        )
        body = self.client.get("/api/guard/status").json()
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["scope"], "whole_site")
        self.assertEqual(body["reason"], "전쟁 속보")

    # ── 관리자 인증 ──────────────────────────────────────────────

    def test_admin_endpoints_require_auth(self):
        cases = [
            ("GET", "/api/admin/guard/status"),
            ("PUT", "/api/admin/guard/status"),
            ("GET", "/api/admin/guard/recommendations"),
            ("POST", "/api/admin/guard/recommendations/1/approve"),
            ("POST", "/api/admin/guard/recommendations/1/reject"),
        ]
        for method, path in cases:
            response = self.client.request(
                method,
                path,
                json={"status": "ok", "scope": "report_generation", "mode": "manual"}
                if method == "PUT"
                else None,
            )
            self.assertEqual(response.status_code, 401, f"{method} {path}")

    # ── 관리자 토글 ──────────────────────────────────────────────

    def test_update_status_blocks_and_audits(self):
        self.login()
        response = self.client.put(
            "/api/admin/guard/status",
            json={
                "status": "blocked",
                "scope": "report_view",
                "mode": "manual",
                "reason": "지정학 리스크 속보",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()["status"]
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["scope"], "report_view")
        self.assertEqual(body["triggered_by"], "admin:admin@example.com")
        self.assertEqual(len(self.connection.audit), 1)
        self.assertEqual(self.connection.audit[0]["action"], "block")
        self.assertEqual(self.connection.audit[0]["actor"], "admin:admin@example.com")

    def test_update_status_unblock_audits_unblock(self):
        self.login()
        self.connection.guard_status["status"] = "blocked"
        response = self.client.put(
            "/api/admin/guard/status",
            json={"status": "ok", "scope": "report_generation", "mode": "advisory"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.connection.audit[0]["action"], "unblock")

    def test_update_status_rejects_invalid_enum(self):
        self.login()
        response = self.client.put(
            "/api/admin/guard/status",
            json={"status": "blocked", "scope": "everything", "mode": "manual"},
        )
        self.assertEqual(response.status_code, 422)

    def test_admin_status_includes_audit(self):
        self.login()
        self.connection.audit.append(
            {
                "action": "block",
                "scope": "whole_site",
                "reason": "테스트",
                "actor": "agent:geo-risk-monitor",
                "created_at": datetime(2026, 7, 3, tzinfo=UTC),
            }
        )
        response = self.client.get("/api/admin/guard/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"]["status"], "ok")
        self.assertEqual(len(body["audit"]), 1)

    # ── 제안 승인/거절 ────────────────────────────────────────────

    def test_approve_recommendation_applies_block(self):
        self.login()
        self.seed_recommendation()
        response = self.client.post("/api/admin/guard/recommendations/1/approve")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["recommendation"]["status"], "approved")
        self.assertEqual(body["status"]["status"], "blocked")
        self.assertEqual(body["status"]["scope"], "report_view")
        self.assertEqual(self.connection.guard_status["status"], "blocked")
        self.assertEqual(len(self.connection.audit), 1)

    def test_reject_recommendation_keeps_status(self):
        self.login()
        self.seed_recommendation()
        response = self.client.post("/api/admin/guard/recommendations/1/reject")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["recommendation"]["status"], "rejected")
        self.assertEqual(self.connection.guard_status["status"], "ok")
        self.assertEqual(len(self.connection.audit), 0)

    def test_approve_non_pending_returns_404(self):
        self.login()
        self.seed_recommendation(status="rejected")
        response = self.client.post("/api/admin/guard/recommendations/1/approve")
        self.assertEqual(response.status_code, 404)

    def test_list_recommendations_filters_by_status(self):
        self.login()
        self.seed_recommendation(recommendation_id=1)
        self.seed_recommendation(recommendation_id=2, status="approved")
        body = self.client.get("/api/admin/guard/recommendations").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["id"], 1)


if __name__ == "__main__":
    unittest.main()
