import os
import unittest
from unittest import mock

from app.core.config import Settings

_DEV_SECRET = "dev-main-server-secret-change-me"
_STRONG = "a-strong-random-production-secret-0123456789"


class ProductionConfigGuardTest(unittest.TestCase):
    """APP_ENV=production 일 때만 켜지는 배포 안전장치(G5/G6)."""

    def _env(self, **overrides) -> dict:
        base = {
            "APP_ENV": "production",
            "AUTH_SECRET_KEY": _STRONG,
            "CORS_ALLOW_ORIGINS": "https://app.example.com",
            "COOKIE_SAMESITE": "none",
            "COOKIE_SECURE": "true",
            "PORTONE_WEBHOOK_SECRET": "whsec_example",
        }
        base.update(overrides)
        return base

    def test_valid_production_config_ok(self) -> None:
        with mock.patch.dict(os.environ, self._env(), clear=False):
            settings = Settings()
        self.assertEqual(settings.app_env, "production")

    def test_dev_secret_raises(self) -> None:  # G5
        with mock.patch.dict(os.environ, self._env(AUTH_SECRET_KEY=_DEV_SECRET), clear=False):
            with self.assertRaises(ValueError):
                Settings()

    def test_wildcard_cors_raises(self) -> None:  # G6
        with mock.patch.dict(os.environ, self._env(CORS_ALLOW_ORIGINS="*"), clear=False):
            with self.assertRaises(ValueError):
                Settings()

    def test_samesite_none_without_secure_raises(self) -> None:  # G6
        with mock.patch.dict(os.environ, self._env(COOKIE_SECURE="false"), clear=False):
            with self.assertRaises(ValueError):
                Settings()

    def test_webhook_secret_required_raises(self) -> None:  # G9
        env = self._env()
        env.pop("PORTONE_WEBHOOK_SECRET")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError):
                Settings()

    def test_development_skips_validation(self) -> None:
        # 로컬/CI(기본 development)는 dev 시크릿이어도 통과해야 한다.
        with mock.patch.dict(
            os.environ, {"APP_ENV": "development", "AUTH_SECRET_KEY": _DEV_SECRET}, clear=False
        ):
            settings = Settings()
        self.assertEqual(settings.app_env, "development")


if __name__ == "__main__":
    unittest.main()
