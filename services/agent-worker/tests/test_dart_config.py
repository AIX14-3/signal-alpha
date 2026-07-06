import os
import unittest
from unittest.mock import patch

from app.core.config import Settings
from app.orchestrator.queue.task_types import (
    ANALYZE_DART,
    COLLECT_DART,
    COLLECT_DART_EMPLOYEE,
    COLLECT_DART_FINANCIALS,
    COLLECT_DART_OWNERSHIP,
    NORMALIZE_DART,
    NORMALIZE_DART_EMPLOYEE,
    NORMALIZE_DART_FINANCIALS,
    NORMALIZE_DART_OWNERSHIP,
)


class DartConfigTest(unittest.TestCase):
    def test_settings_reads_dart_api_options(self):
        env = {
            **os.environ,
            "DART_API_KEY": "test-key",
            "DART_BASE_URL": "https://opendart.example",
            "DART_TIMEOUT_SECONDS": "15",
            "DART_PAGE_SIZE": "50",
            "DART_FETCH_DOCUMENTS": "false",
            "DART_MAX_RETRIES": "4",
            "DART_RETRY_BACKOFF_SECONDS": "0.25",
            "DART_USE_LLM": "true",
            "DART_LLM_HIGH_IMPACT_ONLY": "false",
            "DART_LLM_PROVIDER": "gemini",
            "DART_LLM_MODEL": "test-model",
            "DART_LLM_TIMEOUT_SECONDS": "7.5",
            "OPENAI_API_KEY": "openai-test-key",
            "OPENAI_BASE_URL": "https://openai.example/v1",
            "GEMINI_API_KEY": "gemini-test-key",
            "GEMINI_BASE_URL": "https://gemini.example/v1beta",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = Settings()

        self.assertEqual(settings.dart_api_key, "test-key")
        self.assertEqual(settings.dart_base_url, "https://opendart.example")
        self.assertEqual(settings.dart_timeout_seconds, 15)
        self.assertEqual(settings.dart_page_size, 50)
        self.assertFalse(settings.dart_fetch_documents)
        self.assertEqual(settings.dart_max_retries, 4)
        self.assertEqual(settings.dart_retry_backoff_seconds, 0.25)
        self.assertTrue(settings.dart_use_llm)
        self.assertFalse(settings.dart_llm_high_impact_only)
        self.assertEqual(settings.dart_llm_provider, "gemini")
        self.assertEqual(settings.dart_llm_model, "test-model")
        self.assertEqual(settings.dart_llm_timeout_seconds, 7.5)
        self.assertEqual(settings.openai_api_key, "openai-test-key")
        self.assertEqual(settings.openai_base_url, "https://openai.example/v1")
        self.assertEqual(settings.gemini_api_key, "gemini-test-key")
        self.assertEqual(settings.gemini_base_url, "https://gemini.example/v1beta")

    def test_dart_task_type_constants_are_stable(self):
        self.assertEqual(COLLECT_DART, "collect_dart")
        self.assertEqual(COLLECT_DART_OWNERSHIP, "collect_dart_ownership")
        self.assertEqual(COLLECT_DART_FINANCIALS, "collect_dart_financials")
        self.assertEqual(COLLECT_DART_EMPLOYEE, "collect_dart_employee")
        self.assertEqual(NORMALIZE_DART, "normalize_dart")
        self.assertEqual(NORMALIZE_DART_OWNERSHIP, "normalize_dart_ownership")
        self.assertEqual(NORMALIZE_DART_FINANCIALS, "normalize_dart_financials")
        self.assertEqual(NORMALIZE_DART_EMPLOYEE, "normalize_dart_employee")
        self.assertEqual(ANALYZE_DART, "analyze_dart")
