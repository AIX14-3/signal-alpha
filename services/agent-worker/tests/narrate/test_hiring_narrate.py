"""HiringNarrator 단위 테스트 — PRICE narrator 동형. LLM 서술만, 수치 불변, 조언표현 차단, 폴백."""

from __future__ import annotations

import json
import unittest

from app.narrate.base import NarrateError
from app.narrate.hiring import HiringNarrator

_ANALYSIS = {
    "summary": "최근 90일 채용공고 26건(직전 대비 +67%), 주요 기술: AI.",
    "direction": "unknown",
    "score_100": 50,
    "data_status": "no_signal",
}


class _FakeClient:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        self.calls += 1
        return self._response


class HiringNarratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_parses_summary_and_key_facts(self) -> None:
        client = _FakeClient(
            json.dumps(
                {
                    "summary": "최근 90일 채용공고가 26건으로 직전 대비 67% 늘었습니다. AI 기술 인력을 주로 모집했습니다.",
                    "key_facts": ["채용공고 26건(직전 대비 +67%)", "주요 기술: AI"],
                }
            )
        )
        narrator = HiringNarrator(client=client, model="test-model")

        narrative = await narrator.narrate(
            stock_code="012330", analysis=_ANALYSIS, prediction_rate={"score_100": 50, "direction": "neutral"}
        )

        self.assertEqual(client.calls, 1)
        self.assertIn("채용공고", narrative.summary)
        self.assertEqual(len(narrative.key_facts), 2)

    async def test_advice_language_rejected(self) -> None:
        client = _FakeClient(
            json.dumps({"summary": "채용이 늘어 지금 매수 추천합니다.", "key_facts": []})
        )
        narrator = HiringNarrator(client=client, model="test-model")

        with self.assertRaises(NarrateError):
            await narrator.narrate(stock_code="012330", analysis=_ANALYSIS, prediction_rate=None)

    async def test_empty_analysis_raises(self) -> None:
        client = _FakeClient("{}")
        narrator = HiringNarrator(client=client, model="test-model")

        with self.assertRaises(NarrateError):
            await narrator.narrate(stock_code="012330", analysis=None, prediction_rate=None)
        self.assertEqual(client.calls, 0)  # LLM 호출 전에 차단


if __name__ == "__main__":
    unittest.main()
