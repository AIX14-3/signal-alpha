import os
import unittest
from unittest.mock import patch

from signal_alpha_data_access.database import DatabaseSettings


class DatabaseSettingsTest(unittest.TestCase):
    def test_database_settings_reads_url_from_argument(self):
        settings = DatabaseSettings(
            database_url="postgresql://user:pass@localhost:5432/signal_alpha"
        )

        self.assertEqual(
            settings.database_url,
            "postgresql://user:pass@localhost:5432/signal_alpha",
        )

    def test_database_settings_can_read_url_from_env(self):
        env = {**os.environ, "DATABASE_URL": "postgresql://env-user:env-pass@localhost/db"}

        with patch.dict(os.environ, env, clear=True):
            settings = DatabaseSettings()

        self.assertEqual(settings.database_url, "postgresql://env-user:env-pass@localhost/db")

    def test_from_env_reads_named_variable(self):
        # 2-DB 분리: 백엔드 발행용 DSN을 별도 env 변수에서 읽는다.
        env = {
            **os.environ,
            "DATABASE_URL": "postgresql://collection/db",
            "BACKEND_DATABASE_URL": "postgresql://backend/db",
        }

        with patch.dict(os.environ, env, clear=True):
            collection = DatabaseSettings.from_env("DATABASE_URL")
            backend = DatabaseSettings.from_env("BACKEND_DATABASE_URL", max_pool_size=5)

        self.assertEqual(collection.database_url, "postgresql://collection/db")
        self.assertEqual(backend.database_url, "postgresql://backend/db")
        self.assertEqual(backend.max_pool_size, 5)

    def test_from_env_missing_variable_is_none(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = DatabaseSettings.from_env("BACKEND_DATABASE_URL")

        self.assertIsNone(settings.database_url)
