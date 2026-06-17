"""hiring Record-Level Quarantine(#161, Phase 4) 단위 테스트.

DB 없이 SessionLocal/_insert_one/_quarantine_record 를 패치해 검증:
  - 게이트 거부·_insert_one 오류 시 격리 훅 호출(유효 레코드는 미격리)
  - 하이브리드 수용: dict(legacy)·(dict, raw_payload) 튜플(new) 정규화
  - 격리 best-effort: _quarantine_record 내부 예외가 수집 루프를 죽이지 않음
"""

from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

from app.collectors.hiring.base_collector import BaseCollector, CollectorResult


def _valid(company="삼성전자", title="백엔드 개발자", link="https://x.test/1") -> dict:
    return {"company_name": company, "job_title": title, "job_link": link}


def _invalid() -> dict:
    return {"company_name": "", "job_title": "백엔드", "job_link": "https://x.test/2"}


class _CollectorDouble(BaseCollector):
    """DB 엔진 없이 insert_to_db 흐름만 실행하는 최소 더블."""

    def __init__(self):
        self.database_url = "postgresql://test/ignored"
        self.SessionLocal = MagicMock()

    def collect(self, target_companies):  # pragma: no cover
        return []

    def parse(self, raw_data):  # pragma: no cover
        return raw_data


class QuarantineHookTest(unittest.TestCase):
    def _run(self, records, *, insert_side_effect=None):
        collector = _CollectorDouble()
        patches = {
            "_create_collector_run": patch.object(
                _CollectorDouble, "_create_collector_run", return_value=1
            ),
            "_insert_one": patch.object(
                _CollectorDouble, "_insert_one", side_effect=insert_side_effect
            ),
            "_quarantine_record": patch.object(_CollectorDouble, "_quarantine_record"),
            "_finish_collector_run": patch.object(_CollectorDouble, "_finish_collector_run"),
        }
        started = {k: p.start() for k, p in patches.items()}
        self.addCleanup(mock.patch.stopall)
        collector.insert_to_db(records)
        return started

    def test_gate_reject_is_quarantined(self):
        m = self._run([_valid(), _invalid()])
        # 무효 1건만 격리, 유효 1건은 _insert_one 도달.
        self.assertEqual(m["_quarantine_record"].call_count, 1)
        self.assertEqual(m["_insert_one"].call_count, 1)
        # 격리 인자: (db, run_id, data, raw_payload, reason) — raw_payload=None(legacy dict)
        args = m["_quarantine_record"].call_args.args
        self.assertIsNone(args[3])                       # raw_payload
        self.assertIn("company_name", args[4])           # reason

    def test_insert_error_is_quarantined(self):
        m = self._run([_valid()], insert_side_effect=RuntimeError("db boom"))
        # _insert_one 오류 → 격리 1회, 사유에 _insert_one 표기.
        self.assertEqual(m["_quarantine_record"].call_count, 1)
        self.assertIn("_insert_one", m["_quarantine_record"].call_args.args[4])

    def test_valid_record_not_quarantined(self):
        m = self._run([_valid()])
        m["_quarantine_record"].assert_not_called()
        self.assertEqual(m["_insert_one"].call_count, 1)

    def test_hybrid_tuple_passes_raw_payload(self):
        # (dict, raw_payload) 튜플 입력 → 무효면 raw_payload 가 격리로 전달.
        m = self._run([(_invalid(), "<html>raw</html>")])
        args = m["_quarantine_record"].call_args.args
        self.assertEqual(args[3], "<html>raw</html>")


class QuarantineBestEffortTest(unittest.TestCase):
    def test_quarantine_failure_does_not_break_loop(self):
        """_quarantine_record 내부 INSERT 예외는 삼켜져 수집 루프가 계속된다."""
        collector = _CollectorDouble()
        # db.execute 가 예외 → _quarantine_record 의 try/except 가 흡수해야 함.
        db = collector.SessionLocal.return_value
        db.execute.side_effect = RuntimeError("quarantine insert failed")

        with patch.object(_CollectorDouble, "_create_collector_run", return_value=1), \
             patch.object(_CollectorDouble, "_finish_collector_run"):
            # 무효 레코드 → 격리 시도 → db.execute 예외 → 삼킴. 예외 전파 없이 완주.
            inserted = collector.insert_to_db([_invalid()])

        self.assertEqual(inserted, 0)  # 적재 0, 예외 없이 반환


class CollectorResultTest(unittest.TestCase):
    def test_defaults(self):
        r = CollectorResult(data={"a": 1})
        self.assertEqual(r.data, {"a": 1})
        self.assertIsNone(r.raw_payload)
        self.assertIsNone(r.source_label)

    def test_with_raw_payload(self):
        r = CollectorResult(data={"a": 1}, raw_payload="<html/>", source_label="NAVER_CAREERS")
        self.assertEqual(r.raw_payload, "<html/>")
        self.assertEqual(r.source_label, "NAVER_CAREERS")


if __name__ == "__main__":
    unittest.main()
