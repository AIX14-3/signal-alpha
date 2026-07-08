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
from app.core.rate_limit import FixedWindowLimiter, LoginLockout, client_ip_from_scope
from app.core.social import SocialError, resolve_social_identity
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


def _scope(*, client_ip="203.0.113.9", xff=None):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("latin-1")))
    return {"client": (client_ip, 12345), "headers": headers}


class ClientIpTrustTest(unittest.TestCase):
    """XFF 신뢰 우회 회귀: 클라이언트가 위조한 X-Forwarded-For 로 rate limit/잠금 키를 못 바꾼다."""

    def test_default_ignores_client_supplied_xff(self):
        # trusted_proxies=0(기본) → XFF 위조를 무시하고 소켓 peer 만 신뢰.
        scope = _scope(client_ip="203.0.113.9", xff="1.2.3.4")
        self.assertEqual(client_ip_from_scope(scope, trusted_proxies=0), "203.0.113.9")

    def test_rotating_spoofed_xff_yields_same_key(self):
        # 공격자가 매 요청 XFF 를 바꿔도 키(소켓 IP)는 불변 → 잠금/레이트리밋 우회 불가.
        a = client_ip_from_scope(_scope(client_ip="203.0.113.9", xff="9.9.9.9"), trusted_proxies=0)
        b = client_ip_from_scope(_scope(client_ip="203.0.113.9", xff="8.8.8.8"), trusted_proxies=0)
        self.assertEqual(a, b)

    def test_one_trusted_proxy_strips_only_the_trusted_hop(self):
        # 신뢰 프록시 1홉: 우측 1홉을 벗긴 실클라이언트. 공격자가 좌측에 붙인 위조값은 무시된다.
        scope = _scope(client_ip="10.0.0.1", xff="1.1.1.1, 203.0.113.9, 10.0.0.1")
        self.assertEqual(client_ip_from_scope(scope, trusted_proxies=1), "203.0.113.9")

    def test_short_chain_falls_back_to_leftmost(self):
        scope = _scope(client_ip="10.0.0.1", xff="203.0.113.9")
        self.assertEqual(client_ip_from_scope(scope, trusted_proxies=2), "203.0.113.9")

    def test_unknown_when_no_client(self):
        self.assertEqual(client_ip_from_scope({"headers": []}, trusted_proxies=0), "unknown")


class _FakeSocialSettings:
    def __init__(self, app_env, providers):
        self.app_env = app_env
        self.social_providers = providers


_DEV_PROVIDER = {"naver": {"client_id": None, "client_secret": None}}


class SocialFailClosedTest(unittest.IsolatedAsyncioTestCase):
    """프로덕션에서 소셜 자격 미설정 시 dev 모드(계정 탈취 경로)로 조용히 떨어지지 않는다."""

    async def test_prod_dev_mode_social_is_rejected(self):
        settings = _FakeSocialSettings("production", _DEV_PROVIDER)
        with self.assertRaises(SocialError):
            await resolve_social_identity(settings, "naver", "attacker-chosen-code")

    async def test_dev_mode_social_resolves_in_non_production(self):
        settings = _FakeSocialSettings("development", _DEV_PROVIDER)
        identity = await resolve_social_identity(settings, "naver", "code123")
        self.assertEqual(identity.provider, "naver")
        self.assertTrue(identity.provider_user_id.startswith("naver_"))


if __name__ == "__main__":
    unittest.main()
