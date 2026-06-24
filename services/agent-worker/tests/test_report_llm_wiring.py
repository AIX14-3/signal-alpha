import os
import unittest
from unittest.mock import patch

from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.task_types import ANALYZE_REPORT
from app.orchestrator.report.llm_wiring import build_report_llm_config


class Settings:
    report_use_llm = True
    report_llm_provider = "gemini"
    report_llm_model = "gemini-test-model"
    report_llm_timeout_seconds = 7.5
    gemini_api_key = "gemini-key"
    gemini_base_url = "https://gemini.example/v1beta"
    openai_api_key = "openai-key"
    openai_base_url = "https://openai.example/v1"
    dart_llm_high_impact_only = True


class ReportLlmWiringTest(unittest.TestCase):
    def test_builds_gemini_report_llm_config_from_settings(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
            config = build_report_llm_config(Settings())

        self.assertIsNotNone(config)
        self.assertIsInstance(config.client, GeminiGenerateContentClient)
        self.assertEqual(config.model, "gemini-test-model")
        self.assertEqual(config.timeout_seconds, 7.5)

    def test_builds_openai_report_llm_config_from_settings(self):
        settings = Settings()
        settings.report_llm_provider = "openai"
        settings.report_llm_model = "gpt-test-model"

        with patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
            config = build_report_llm_config(settings)

        self.assertIsNotNone(config)
        self.assertIsInstance(config.client, OpenAiChatClient)
        self.assertEqual(config.model, "gpt-test-model")

    def test_skips_report_llm_when_disabled_or_incomplete(self):
        disabled = Settings()
        disabled.report_use_llm = False
        self.assertIsNone(build_report_llm_config(disabled))

        missing_model = Settings()
        missing_model.report_llm_model = ""
        self.assertIsNone(build_report_llm_config(missing_model))

        missing_key = Settings()
        missing_key.gemini_api_key = ""
        self.assertIsNone(build_report_llm_config(missing_key))

        unsupported = Settings()
        unsupported.report_llm_provider = "unknown"
        self.assertIsNone(build_report_llm_config(unsupported))

    def test_queue_handler_wires_report_llm_config_into_analyze_handler(self):
        settings = Settings()

        with patch("app.orchestrator.queue.handlers.get_settings", return_value=settings):
            with patch.dict(os.environ, {"LANGSMITH_TRACING": "false"}):
                handlers = build_task_handlers(object())

        analyze_handler = handlers[ANALYZE_REPORT]
        agent = analyze_handler._agent
        self.assertIsInstance(agent._llm_client, GeminiGenerateContentClient)
        self.assertEqual(agent._llm_model, "gemini-test-model")
        self.assertEqual(agent._timeout_seconds, 7.5)


if __name__ == "__main__":
    unittest.main()
