"""리스크 veto: 치명 키워드 스캔(순수) + RISK_VETO 핸들러(발행 보류)."""

from __future__ import annotations

import unittest

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
    def __init__(self, event_rows):
        self._event_rows = event_rows
        self.veto_applied_id = None
        self.validation_logged = None

    async def fetch(self, sql, *args):  # list_signal_events_by_ids
        return self._event_rows

    async def fetchrow(self, sql, *args):  # apply_risk_veto UPDATE ... RETURNING
        self.veto_applied_id = args[0]
        return {"id": args[0], "is_published": False, "warning_level": "WARNING"}

    async def fetchval(self, sql, *args):
        if "validation_logs" in sql:  # record_validation_log INSERT ... RETURNING id
            self.validation_logged = args  # (target_type, target_id_int, ...)
            return 1
        return 777  # processing_queue enqueue (dedupe SELECT hit → returns existing id)


class RiskVetoTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_vetoes_published_signal_on_fatal_keyword(self):
        rows = [
            {"title": "감사보고서", "summary": "감사의견거절", "evidence_text": "계속기업 불확실성"},
        ]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(connection)

        result = await handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101, 102],
                "task_context": {"final_signal_id": 55},
            }
        )

        self.assertTrue(result["vetoed"])
        self.assertTrue(result["applied"])
        self.assertIn("감사의견거절", result["matched_keywords"])
        self.assertEqual(connection.veto_applied_id, 55)  # apply_risk_veto called with id
        self.assertEqual(connection.validation_logged[0], "final_signal")  # validation log target

    async def test_clean_signal_is_not_vetoed(self):
        rows = [{"title": "신규 수주", "summary": "매출 성장", "evidence_text": "흑자 전환"}]
        connection = _FakeConnection(rows)
        handler = RiskVetoTaskHandler(connection)

        result = await handler(
            {"stock_id": 10, "source_signal_event_ids": [101], "task_context": {"final_signal_id": 55}}
        )

        self.assertFalse(result["vetoed"])
        self.assertFalse(result["applied"])
        self.assertIsNone(connection.veto_applied_id)

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
