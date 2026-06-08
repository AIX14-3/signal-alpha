import os
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.orchestrator.task_types import COLLECT_DART, ANALYZE_DART, NORMALIZE_DART


class DartConfigTest(unittest.TestCase):
    def test_settings_reads_dart_api_options(self):
        env = {
            **os.environ,
            "DART_API_KEY": "test-key",
            "DART_BASE_URL": "https://opendart.example",
            "DART_TIMEOUT_SECONDS": "15",
            "DART_PAGE_SIZE": "50",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = Settings()

        self.assertEqual(settings.dart_api_key, "test-key")
        self.assertEqual(settings.dart_base_url, "https://opendart.example")
        self.assertEqual(settings.dart_timeout_seconds, 15)
        self.assertEqual(settings.dart_page_size, 50)

    def test_dart_task_type_constants_are_stable(self):
        self.assertEqual(COLLECT_DART, "collect_dart")
        self.assertEqual(NORMALIZE_DART, "normalize_dart")
        self.assertEqual(ANALYZE_DART, "analyze_dart")
