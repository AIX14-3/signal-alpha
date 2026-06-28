"""리스크 veto: 치명 키워드 스캔(순수) + RISK_VETO 핸들러(발행 보류)."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from app.gates.risk_veto import RiskVetoTaskHandler, scan_for_veto
from app.gates.rules.veto_keywords import veto_keywords


def test_scan_detects_fatal_keyword() -> None:
    decision = scan_for_veto(["삼성전자 주요사항보고서", "감사의견거절 발생", None])
    assert decision.vetoed is True
    assert "감사의견거절" in decision.matched_keywords


def test_scan_passes_benign_text() -> None:
    decision = scan_for_veto(["분기 매출 증가", "신규 수주 공시", None])
    assert decision.vetoed is False
    assert decision.matched_keywords == []


def test_scan_handles_empty_texts() -> None:
    assert scan_for_veto([None, "", None]).vetoed is False


def test_scan_with_explicit_keywords_overrides_defaults() -> None:
    decision = scan_for_veto(["특수상황 발생"], keywords=["특수상황"])
    assert decision.vetoed is True
    assert decision.matched_keywords == ["특수상황"]


def test_scan_ignores_resolved_context() -> None:
    # 사건이 해소/부인된 문맥은 veto하지 않는다(오탐 방지).
    assert scan_for_veto(["상장폐지 우려가 해소되었다"]).vetoed is False
    assert scan_for_veto(["횡령 혐의에 대해 무혐의 처분을 받았다"]).vetoed is False
    assert scan_for_veto(["분식회계 의혹은 사실무근으로 밝혀졌다"]).vetoed is False


def test_scan_still_vetoes_real_events() -> None:
    # 해소어가 없는 진짜 치명 사건은 그대로 veto (recall 보존).
    assert scan_for_veto(["상장폐지 결정 공시"]).vetoed is True
    assert scan_for_veto(["분식회계 적발"]).vetoed is True
    assert scan_for_veto(["파산"]).vetoed is True  # 단독 등장 = fail-safe veto


def test_scan_vetoes_when_any_occurrence_is_unnegated() -> None:
    # 한 번은 해소 문맥, 다른 한 번은 실제 사건 → 비부정 등장이 있으므로 veto.
    decision = scan_for_veto(["횡령 혐의 무혐의", "신규 횡령 적발"])
    assert decision.vetoed is True
    assert "횡령" in decision.matched_keywords


def test_env_keywords_are_added(monkeypatch) -> None:
    monkeypatch.setenv("RISK_VETO_KEYWORDS", "유상증자취소, 최대주주변경")
    keywords = veto_keywords()
    assert "유상증자취소" in keywords
    assert "최대주주변경" in keywords
    # defaults still present
    assert "상장폐지" in keywords


class _FakeConnection:
    def __init__(self, event_rows, final_signal=None):
        self._event_rows = event_rows
        self._final_signal = final_signal or {
            "id": 55, "summary": None, "bull_point": None, "bear_point": None,
        }
        self.veto_applied_id = None
        self.validation_logged = None
        self.enqueued = []  # processing_queue INSERT args

    async def fetch(self, sql, *args):  # list_signal_events_by_ids
        return self._event_rows

    async def fetchrow(self, sql, *args):
        if "FROM final_signals WHERE id" in sql:  # get_final_signal_by_id (읽기, LLM 텍스트 검사)
            return self._final_signal
        self.veto_applied_id = args[0]  # apply_risk_veto UPDATE ... RETURNING
        return {"id": args[0], "is_published": False, "warning_level": "WARNING"}

    async def fetchval(self, sql, *args):
        if "validation_logs" in sql:  # record_validation_log INSERT ... RETURNING id
            self.validation_logged = args  # (target_type, target_id_int, ...)
            return 1
        if "INSERT INTO processing_queue" in sql:
            self.enqueued.append(args)  # (stock_id, task_type, priority, ...)
            return 999
        return None  # dedupe SELECT → 신규


class RiskVetoTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_fatal_keyword_first_pass_triggers_refine_when_llm_on(self):
        # LLM 종합이 켜져 있으면 — 치명 키워드라도 미발행으로 버리지 않고 정제(refine)로 돌린다.
        rows = [
            {"title": "감사보고서", "summary": "감사의견거절", "evidence_text": "계속기업 불확실성"},
        ]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(connection, settings=SimpleNamespace(gemini_api_key="k"))

        with mock.patch.dict(
            os.environ,
            {
                "SYNTHESIS_USE_LLM": "1",
                "SYNTHESIS_LLM_MODEL": "test-model",
                "SYNTHESIS_LLM_PROVIDER": "gemini",
            },
        ):
            result = await handler(
                {
                    "stock_id": 10,
                    "source_signal_event_ids": [101, 102],
                    "task_context": {"final_signal_id": 55},
                }
            )

        self.assertTrue(result["vetoed"])
        self.assertFalse(result["applied"])  # 미발행 아님
        self.assertFalse(result["refined"])
        self.assertIn("감사의견거절", result["matched_keywords"])
        self.assertIsNone(connection.veto_applied_id)  # apply_risk_veto 호출 안 됨
        # SYNTHESIZE(refine=true)를 인큐해 LLM 정제로 돌린다.
        import json
        synth = [a for a in connection.enqueued if a[1] == "synthesize"]
        self.assertEqual(len(synth), 1)
        self.assertTrue(json.loads(synth[0][6])["refine"])

    async def test_fatal_keyword_first_pass_holds_directly_when_llm_off(self):
        # LLM 미구성(기본 배포)이면 정제는 무동작이므로 정제 왕복 없이 곧장 발행 보류.
        rows = [{"title": "감사보고서", "summary": "감사의견거절", "evidence_text": ""}]
        connection = _FakeConnection(rows)
        # settings에 api 키 없음 → synthesis_llm_enabled=False → 정제 생략.
        handler = RiskVetoTaskHandler(connection, settings=SimpleNamespace())

        result = await handler(
            {"stock_id": 10, "source_signal_event_ids": [101], "task_context": {"final_signal_id": 55}}
        )

        self.assertTrue(result["vetoed"])
        self.assertTrue(result["applied"])  # 곧장 보류
        self.assertFalse(result["refined"])
        self.assertEqual(connection.veto_applied_id, 55)
        self.assertEqual([a for a in connection.enqueued if a[1] == "synthesize"], [])  # 정제 생략

    async def test_fatal_keyword_after_refine_holds_publication(self):
        # 정제(refine) 후에도 치명적이면 그때 발행 보류(needs_review).
        rows = [{"title": "감사보고서", "summary": "감사의견거절", "evidence_text": ""}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101],
                "task_context": {"final_signal_id": 55, "refined": True},
            }
        )

        self.assertTrue(result["vetoed"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["refined"])
        self.assertEqual(connection.veto_applied_id, 55)
        self.assertEqual(connection.validation_logged[0], "final_signal")
        self.assertEqual([a for a in connection.enqueued if a[1] == "synthesize"], [])  # 루프 종료

    async def test_clean_signal_is_not_vetoed(self):
        rows = [{"title": "신규 수주", "summary": "매출 성장", "evidence_text": "흑자 전환"}]
        connection = _FakeConnection(rows)
        # 백엔드 미설정(단일 DB) → 발행 인큐 없음(핸들러 no-op과 동일).
        handler = RiskVetoTaskHandler(connection, settings=SimpleNamespace())

        result = await handler(
            {"stock_id": 10, "source_signal_event_ids": [101], "task_context": {"final_signal_id": 55}}
        )

        self.assertFalse(result["vetoed"])
        self.assertFalse(result["applied"])
        self.assertIsNone(connection.veto_applied_id)
        self.assertIsNone(result["publish_task_id"])
        self.assertEqual([a for a in connection.enqueued if a[1] == "publish_signals"], [])

    async def test_clean_signal_enqueues_publish_after_veto_gate(self):
        # 핵심 수정: veto 통과 신호의 백엔드 발행(PUBLISH_SIGNALS)은 AGGREGATE 가 아니라 이
        # RISK_VETO 게이트 뒤에서 인큐된다(치명 신호가 veto 전에 백엔드로 새는 것을 막음).
        rows = [{"title": "신규 수주", "summary": "매출 성장", "evidence_text": "흑자 전환"}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(
            connection, settings=SimpleNamespace(backend_database_url="postgresql://backend/db")
        )

        result = await handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101],
                "task_context": {"final_signal_id": 55, "stock_code": "005930"},
            }
        )

        self.assertFalse(result["vetoed"])
        self.assertIsNotNone(result["publish_task_id"])
        publish = [a for a in connection.enqueued if a[1] == "publish_signals"]
        self.assertEqual(len(publish), 1)
        self.assertEqual(publish[0][0], 10)  # stock_id

    async def test_publish_preserves_immediate_priority(self):
        # priority 가 체인(…→RISK_VETO→PUBLISH)으로 전파돼 immediate 발행이 batch 로 강등되지 않는다.
        rows = [{"title": "신규 수주", "summary": "매출 성장", "evidence_text": "흑자 전환"}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(
            connection, settings=SimpleNamespace(backend_database_url="postgresql://backend/db")
        )

        await handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101],
                "task_context": {"final_signal_id": 55, "stock_code": "005930", "priority": "immediate"},
            }
        )

        publish = [a for a in connection.enqueued if a[1] == "publish_signals"]
        self.assertEqual(publish[0][2], "immediate")  # priority 인자(강등 X)

    async def test_vetoed_signal_does_not_enqueue_publish(self):
        # 치명 키워드 신호는 backend 설정과 무관하게 발행되지 않는다(미발행 보류).
        rows = [{"title": "감사보고서", "summary": "감사의견거절", "evidence_text": ""}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(
            connection, settings=SimpleNamespace(backend_database_url="postgresql://backend/db")
        )

        result = await handler(
            {"stock_id": 10, "source_signal_event_ids": [101], "task_context": {"final_signal_id": 55}}
        )

        self.assertTrue(result["vetoed"])
        self.assertTrue(result["applied"])  # LLM 미구성 → 곧장 보류
        self.assertIsNone(result["publish_task_id"])
        self.assertEqual([a for a in connection.enqueued if a[1] == "publish_signals"], [])

    async def test_skips_without_signal_events(self):
        connection = _FakeConnection([])
        handler = RiskVetoTaskHandler(connection)
        result = await handler({"stock_id": 10, "task_context": {"final_signal_id": 55}})
        self.assertEqual(result["skipped_reason"], "no_signal_events")

    async def test_vetoed_without_final_signal_id_does_not_apply(self):
        rows = [{"title": "상장폐지 사유 발생", "summary": "", "evidence_text": ""}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(connection)

        result = await handler({"stock_id": 10, "source_signal_event_ids": [1], "task_context": {}})

        self.assertTrue(result["vetoed"])
        self.assertFalse(result["applied"])
        self.assertIsNone(connection.veto_applied_id)


if __name__ == "__main__":
    unittest.main()
