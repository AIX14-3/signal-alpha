"""보안 강화 회귀 테스트: 보안 헤더, 인메모리 레이트리밋, 로그인 잠금."""
import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.health import get_database_pool
from app.core.rate_limit import FixedWindowLimiter, LoginLockout
from app.main import app


class _FakeConnection:
    async def fetchval(self, *args, **kwargs):
        return 1


class _FakeAcquire:
    async def __aenter__(self) -> "_FakeConnection":
        return _FakeConnection()

    async def __aexit__(self, *args) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


class SecurityHeadersTest(unittest.TestCase):
    def setUp(self):
        # /health 는 DB 연결을 검증한다 → 도달 가능한 풀을 주입해야 200 이 떨어진다.
        # 보안 헤더는 상태코드와 무관하게 모든 응답에 붙지만, 200 경로에서 함께 검증한다.
        app.dependency_overrides[get_database_pool] = lambda: _FakePool()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_database_pool, None)

    def test_security_headers_present(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("x-frame-options"), "DENY")
        self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(res.headers.get("referrer-policy"), "no-referrer")
        self.assertEqual(res.headers.get("content-security-policy"), "frame-ancestors 'none'")
        self.assertIn("camera=()", res.headers.get("permissions-policy", ""))

    def test_bad_host_rejected(self):
        res = self.client.get("/health", headers={"host": "evil.example.com"})
        self.assertEqual(res.status_code, 400)


class FixedWindowLimiterTest(unittest.TestCase):
    def test_allows_up_to_max_then_blocks(self):
        limiter = FixedWindowLimiter(max_requests=3, window_seconds=60)
        self.assertTrue(all(limiter.allow("k") for _ in range(3)))
        self.assertFalse(limiter.allow("k"))  # 4번째 차단

    def test_keys_are_independent(self):
        limiter = FixedWindowLimiter(max_requests=1, window_seconds=60)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        self.assertFalse(limiter.allow("a"))

    def test_reset(self):
        limiter = FixedWindowLimiter(max_requests=1, window_seconds=60)
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))
        limiter.reset("k")
        self.assertTrue(limiter.allow("k"))


class LoginLockoutTest(unittest.TestCase):
    def test_locks_after_max_failures(self):
        lockout = LoginLockout(max_failures=3, lock_seconds=300)
        self.assertEqual(lockout.retry_after("u"), 0)
        for _ in range(3):
            lockout.record_failure("u")
        self.assertGreater(lockout.retry_after("u"), 0)  # 잠금됨

    def test_reset_clears_lock_state(self):
        lockout = LoginLockout(max_failures=2, lock_seconds=300)
        lockout.record_failure("u")
        lockout.reset("u")  # 성공 로그인 시 해제
        lockout.record_failure("u")
        self.assertEqual(lockout.retry_after("u"), 0)  # 카운트 리셋되어 아직 잠금 아님


if __name__ == "__main__":
    unittest.main()
