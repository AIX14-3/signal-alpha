import sys
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.main import app

_HEADERS = {"X-Internal-Token": "test-internal-token"}
_TRADE_PAYLOAD = {
    "scope": "trade",
    "stock": {"stock_code": "005930", "stock_name": "삼성전자"},
    "has_plan": True,
    "round_trips": [
        {
            "realized_pnl_pct": -3.2,
            "holding_days": 12,
            "classification": {"verdict": "saw_but_held"},
            "plan_vs_actual": {"has_plan": True},
            "observed_signals": [{"signal_date": "2026-03-02", "kind": "insider_disposal"}],
        }
    ],
}


class FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        return self._text


class PostmortemNarrateRouteTest(unittest.TestCase):
    def _client(self) -> TestClient:
        return TestClient(app, headers=_HEADERS)

    def test_disabled_when_no_model_returns_null(self):
        # POSTMORTEM_LLM_MODEL 미설정 → 내러티브 비활성(null), 실패 아님.
        with patch("app.api.routes.postmortem.narrate_client_from_env", return_value=None):
            response = self._client().post("/internal/postmortem/narrate", json=_TRADE_PAYLOAD)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["narrative"])

    def test_returns_narrative_on_success(self):
        fake = FakeLLM('{"summary": "계획 대비 아쉬웠던 복기입니다.", "key_facts": ["내부자 처분 신호가 있었습니다."]}')
        with patch(
            "app.api.routes.postmortem.narrate_client_from_env", return_value=(fake, "fake-model", 5.0)
        ):
            response = self._client().post("/internal/postmortem/narrate", json=_TRADE_PAYLOAD)
        self.assertEqual(response.status_code, 200)
        narrative = response.json()["narrative"]
        self.assertEqual(narrative["summary"], "계획 대비 아쉬웠던 복기입니다.")
        self.assertEqual(narrative["model"], "fake-model")
        self.assertIn("내부자 처분 신호가 있었습니다.", narrative["key_facts"])

    def test_advice_language_rejected_returns_null(self):
        # 서술에 투자 권유가 섞이면 reject_advice → NarrateError → null(가드).
        fake = FakeLLM('{"summary": "지금 매수 추천합니다.", "key_facts": []}')
        with patch(
            "app.api.routes.postmortem.narrate_client_from_env", return_value=(fake, "fake-model", 5.0)
        ):
            response = self._client().post("/internal/postmortem/narrate", json=_TRADE_PAYLOAD)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["narrative"])

    def test_empty_payload_returns_null(self):
        fake = FakeLLM('{"summary": "x", "key_facts": []}')
        with patch(
            "app.api.routes.postmortem.narrate_client_from_env", return_value=(fake, "fake-model", 5.0)
        ):
            response = self._client().post(
                "/internal/postmortem/narrate",
                json={"scope": "trade", "round_trips": [], "patterns": None},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["narrative"])

    def test_requires_internal_token(self):
        response = TestClient(app).post("/internal/postmortem/narrate", json=_TRADE_PAYLOAD)
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
