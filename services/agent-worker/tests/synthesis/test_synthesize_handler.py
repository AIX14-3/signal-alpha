"""SynthesizeTaskHandler: 결정론 폴백 / LLM / LLM 실패 폴백 + 수치 불변."""

from __future__ import annotations

import unittest

from app.synthesis.synthesizer import RiskNarrative
from app.synthesis.tasks import SynthesizeTaskHandler


class _FakeConnection:
    """SQL 내용으로 라우팅하는 가짜 연결."""

    def __init__(self, final_signal, events, meta=None):
        self._final_signal = final_signal
        self._events = events
        self._meta = meta
        self.narrative_update = None

    async def fetch(self, sql, *args):  # list_signal_events_by_ids
        return self._events

    async def fetchrow(self, sql, *args):
        if "UPDATE final_signals" in sql:
            self.narrative_update = args  # (id, summary, bull, bear)
            return {"id": args[0]}
        if "FROM final_signals WHERE id" in sql:
            return self._final_signal
        if "FROM meta_signals" in sql:
            return self._meta
        return None

    async def fetchval(self, sql, *args):  # RISK_VETO enqueue (dedupe SELECT + INSERT)
        return 999 if "INSERT INTO processing_queue" in sql else None


_FINAL = {
    "id": 55,
    "signal": "negative",
    "final_score": 22.0,
    "confidence": 40.0,
    "warning_level": "WARNING",
    "is_published": False,
    "needs_review": True,
    "signal_date": "2026-06-22",
}
_EVENTS = [
    {"source_type": "DART", "title": "감사보고서", "summary": "감사의견거절", "impact_level": "high",
     "evidence_url": "http://x"},
]


class _OkSynth:
    async def synthesize(self, context):
        return RiskNarrative(headline="설명", narrative="근거 설명", key_points=["p"], caution_points=["c"])


class _BoomSynth:
    async def synthesize(self, context):
        raise RuntimeError("llm down")


class SynthesizeTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, connection, synthesizer):
        handler = SynthesizeTaskHandler(connection, settings=None, synthesizer=synthesizer)
        return await handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101],
                "task_context": {
                    "final_signal_id": 55,
                    "stock_code": "005930",
                    "vetoed": True,
                    "matched_keywords": ["감사의견거절"],
                },
            }
        )

    async def test_deterministic_fallback_when_no_llm(self):
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        result = await self._run(connection, synthesizer=None)

        self.assertEqual(result["narrative_source"], "deterministic")
        report = result["report"]
        # 수치/판정은 final_signal 값 그대로(LLM/폴백이 못 바꿈)
        self.assertEqual(report["signal"], "negative")
        self.assertEqual(report["final_score"], 22.0)
        self.assertFalse(report["is_published"])
        self.assertTrue(report["vetoed"])
        # veto 사유가 caution에 설명됨
        self.assertTrue(any("감사의견거절" in c for c in report["narrative"]["caution_points"]))
        # 결정론 폴백은 집계 요약을 덮어쓰지 않는다(DB 미갱신).
        self.assertFalse(result["narrative_persisted"])
        self.assertIsNone(connection.narrative_update)

    async def test_llm_narrative_source(self):
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        result = await self._run(connection, synthesizer=_OkSynth())
        self.assertEqual(result["narrative_source"], "llm")
        self.assertEqual(result["report"]["narrative"]["headline"], "설명")
        # 수치는 여전히 결정론 값
        self.assertEqual(result["report"]["signal"], "negative")
        # LLM 산출일 때만 final_signal 요약 갱신
        self.assertTrue(result["narrative_persisted"])
        self.assertEqual(connection.narrative_update[0], 55)

    async def test_negative_signal_maps_key_points_to_bear(self):
        # negative 신호: 핵심 근거(key_points)가 bear_point 로, bull_point 는 비움.
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        await self._run(connection, synthesizer=_OkSynth())
        _id, _summary, bull_point, bear_point = connection.narrative_update
        self.assertIsNone(bull_point)
        self.assertEqual(bear_point, "p; c")

    async def test_positive_signal_maps_key_points_to_bull(self):
        positive = {**_FINAL, "signal": "positive", "is_published": True}
        connection = _FakeConnection(positive, list(_EVENTS))
        await self._run(connection, synthesizer=_OkSynth())
        _id, _summary, bull_point, bear_point = connection.narrative_update
        self.assertEqual(bull_point, "p")
        self.assertEqual(bear_point, "c")

    async def test_llm_failure_falls_back(self):
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        result = await self._run(connection, synthesizer=_BoomSynth())
        self.assertEqual(result["narrative_source"], "llm_fallback")
        self.assertEqual(result["report"]["signal"], "negative")
        # 폴백은 DB 요약을 덮어쓰지 않는다
        self.assertFalse(result["narrative_persisted"])
        self.assertIsNone(connection.narrative_update)

    async def test_includes_ml_risk_when_meta_present(self):
        meta = {"combined_vol": 0.31, "confidence": 0.8, "method": "stacking"}
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS), meta=meta)
        result = await self._run(connection, synthesizer=None)
        self.assertEqual(result["report"]["ml_risk"]["combined_vol"], 0.31)
        self.assertEqual(result["report"]["ml_risk"]["method"], "stacking")

    async def test_requires_final_signal_id(self):
        connection = _FakeConnection(dict(_FINAL), [])
        handler = SynthesizeTaskHandler(connection, settings=None, synthesizer=None)
        result = await handler({"stock_id": 10, "task_context": {}})
        self.assertEqual(result["skipped_reason"], "final_signal_id_required")


if __name__ == "__main__":
    unittest.main()
