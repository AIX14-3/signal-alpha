"""SynthesizeTaskHandler: 결정론 폴백 / LLM / LLM 실패 폴백 + 수치 불변."""

from __future__ import annotations

import unittest

from app.synthesis.synthesizer import RiskNarrative
from app.synthesis.tasks import SynthesizeTaskHandler


class _FakeConnection:
    """SQL 내용으로 라우팅하는 가짜 연결."""

    def __init__(self, final_signal, events):
        self._final_signal = final_signal
        self._events = events
        self.narrative_update = None

    async def fetch(self, sql, *args):  # list_signal_events_by_ids
        return self._events

    async def fetchrow(self, sql, *args):
        if "UPDATE final_signals" in sql:
            self.narrative_update = args  # (id, summary, bull, bear)
            return {"id": args[0]}
        if "FROM final_signals WHERE id" in sql:
            return self._final_signal
        return None

    async def fetchval(self, sql, *args):  # PUBLISH_SIGNALS enqueue (settings=None 이라 미호출)
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


class _CapturingSynth:
    """LLM 컨텍스트를 기록해 검증."""

    def __init__(self):
        self.context = None

    async def synthesize(self, context):
        self.context = context
        return RiskNarrative(headline="h", narrative="n", key_points=["p"], caution_points=["c"])


_SOURCE_PREDICTIONS = {
    "SRC": {"final_score": 0.03, "direction": "positive", "confidence": 0.6, "model_count": 3},
    "SRC_PRICE": {"final_score": 0.02, "direction": "positive", "confidence": 0.5, "model_count": 1},
    "SRC_DATALAB": {"final_score": 0.04, "direction": "positive", "confidence": 1.0, "model_count": 2},
}


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
        # RISK_VETO 폐기 — vetoed 는 항상 False(발행 차단 게이트 없음).
        self.assertFalse(report["vetoed"])
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

    async def test_ml_risk_is_none_after_vol_channel_removed(self):
        # vol(변동성) ML 채널 폐기(#585) — ml_risk 는 항상 None.
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        self.assertIsNone(result["report"]["ml_risk"])

    async def test_price_prediction_surfaced_separately(self):
        # score_breakdown 의 PRICE 항목이 주가 단독 예측으로 분리돼 리포트/내러티브에 노출된다.
        final = {
            **_FINAL,
            "score_breakdown": {
                "PRICE": {
                    "direction": "positive",
                    "score_100": 59.0,
                    "score": 0.18,
                    "data_status": "ok",
                    "summary": "5일 추세 상승",
                },
                "DART": {"direction": "negative", "score_100": 22.0, "data_status": "ok"},
            },
        }
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        pp = result["report"]["price_prediction"]
        self.assertEqual(pp["direction"], "positive")
        self.assertEqual(pp["score_100"], 59.0)
        # 결정론 내러티브 첫 key_point 로 주가예측이 별도 노출된다(사람이 읽는 문장으로).
        points = result["report"]["narrative"]["key_points"]
        self.assertTrue(any("주가만 놓고 본 예측" in p for p in points))
        # 내부 코드(positive)를 그대로 노출하지 않는다.
        self.assertTrue(any("긍정 방향" in p for p in points))

    async def test_price_prediction_none_when_price_missing(self):
        final = {**_FINAL, "score_breakdown": {"PRICE": {"data_status": "missing"}}}
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        self.assertIsNone(result["report"]["price_prediction"])

    async def test_report_valuation_surfaced_for_llm(self):
        # REPORT 밸류에이션 facts 가 score_breakdown 에서 분리돼 리포트/LLM 컨텍스트로 전달된다.
        final = {
            **_FINAL,
            "score_breakdown": {
                "REPORT": {
                    "direction": "unknown",
                    "data_status": "no_signal",
                    "valuation": {"target_price": 90000, "methodology": "PER", "needs_review": False},
                },
            },
        }
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        rv = result["report"]["report_valuation"]
        self.assertEqual(rv["target_price"], 90000)
        self.assertEqual(rv["methodology"], "PER")

    async def test_report_valuation_none_when_absent(self):
        final = {**_FINAL, "score_breakdown": {"DART": {"direction": "unknown"}}}
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        self.assertIsNone(result["report"]["report_valuation"])

    async def test_source_predictions_surfaced_in_report_and_context(self):
        # 7개 예측률(주가 BASE ⊕ 대체데이터)이 리포트 JSON + LLM 컨텍스트에 노출(수치 불변, C안 P4).
        final = {**_FINAL, "source_predictions": dict(_SOURCE_PREDICTIONS)}
        synth = _CapturingSynth()
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=synth)
        self.assertEqual(result["report"]["source_predictions"]["SRC"]["direction"], "positive")
        self.assertIn("source_predictions", synth.context)
        self.assertEqual(synth.context["source_predictions"]["SRC_DATALAB"]["final_score"], 0.04)
        # 수치/판정은 여전히 결정론 값(불변).
        self.assertEqual(result["report"]["final_score"], 22.0)

    async def test_source_predictions_absent_keeps_legacy_output(self):
        # 없으면 리포트/컨텍스트에 키가 없다(하위호환 — 기존 출력과 동일).
        synth = _CapturingSynth()
        connection = _FakeConnection(dict(_FINAL), list(_EVENTS))
        result = await self._run(connection, synthesizer=synth)
        self.assertNotIn("source_predictions", result["report"])
        self.assertNotIn("source_predictions", synth.context)

    async def test_source_predictions_in_deterministic_narrative(self):
        final = {**_FINAL, "source_predictions": dict(_SOURCE_PREDICTIONS)}
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        points = result["report"]["narrative"]["key_points"]
        self.assertTrue(any("주가와 대체데이터를 합친 예측" in p for p in points))

    async def test_source_freshness_surfaced_when_reused(self):
        # last-known 재사용 — data_age_days>0 인 소스만 신선도로 노출(missing/0 제외).
        final = {
            **_FINAL,
            "score_breakdown": {
                "DART": {"direction": "positive", "data_status": "ok", "data_age_days": 5},
                "PRICE": {"direction": "neutral", "data_status": "ok", "data_age_days": 0},
                "HIRING": {"data_status": "missing", "data_age_days": 9},
            },
        }
        synth = _CapturingSynth()
        connection = _FakeConnection(final, list(_EVENTS))
        result = await self._run(connection, synthesizer=synth)
        self.assertEqual(result["report"]["source_freshness"], {"DART": 5})
        self.assertEqual(synth.context["source_freshness"], {"DART": 5})

    async def test_source_freshness_absent_when_all_fresh(self):
        final = {**_FINAL, "score_breakdown": {"DART": {"data_status": "ok", "data_age_days": 0}}}
        connection = _FakeConnection(dict(final), list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        self.assertNotIn("source_freshness", result["report"])

    async def test_source_freshness_in_deterministic_caution(self):
        final = {**_FINAL, "score_breakdown": {"DART": {"data_status": "ok", "data_age_days": 3}}}
        connection = _FakeConnection(dict(final), list(_EVENTS))
        result = await self._run(connection, synthesizer=None)
        self.assertTrue(
            any("최종 업데이트" in c for c in result["report"]["narrative"]["caution_points"])
        )

    async def test_requires_final_signal_id(self):
        connection = _FakeConnection(dict(_FINAL), [])
        handler = SynthesizeTaskHandler(connection, settings=None, synthesizer=None)
        result = await handler({"stock_id": 10, "task_context": {}})
        self.assertEqual(result["skipped_reason"], "final_signal_id_required")


if __name__ == "__main__":
    unittest.main()
