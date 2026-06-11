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
