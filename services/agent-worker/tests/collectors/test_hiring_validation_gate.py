"""hiring 입력 검증 게이트(#147, Phase 3c) 단위 테스트.

세 층위를 검증한다:
  1. validation_failure_reason 순수 함수 — 필수 필드/플레이스홀더/위양성 가드.
  2. insert_to_db 게이트 통합 — 무효 레코드가 failed 로 집계되고 _insert_one 엔
     유효 레코드만 도달하는지(DB 없이 패치로).
  3. MultiSourceCrawler.parse 회귀 — 관대한 기본값이 더 이상 주입되지 않는지.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.collectors.hiring.base_collector import (
    BaseCollector,
    validation_failure_reason,
)
from app.collectors.hiring.multi_source_crawler import MultiSourceCrawler


def _valid(company="삼성전자", title="백엔드 개발자", link="https://x.test/1") -> dict:
    return {"company_name": company, "job_title": title, "job_link": link}


class ValidationReasonTest(unittest.TestCase):
    def test_valid_record_passes(self):
        self.assertIsNone(validation_failure_reason(_valid()))

    def test_source_url_fallback_is_valid(self):
        """job_link 없어도 source_url 있으면 통과(폴백 규칙 = _insert_one 과 동일)."""
        data = {"company_name": "네이버", "job_title": "프론트", "source_url": "https://x.test/2"}
        self.assertIsNone(validation_failure_reason(data))

    def test_missing_company_name(self):
        for company in ("", None, "   "):
            data = _valid(company=company)
            self.assertIn("company_name", validation_failure_reason(data) or "")

    def test_missing_or_placeholder_title(self):
        # 빈/None/공백 + 한글 플레이스홀더 + JS 리터럴(대소문자 변형) 모두 거부.
        for title in ("", None, "   ", "(제목 없음)", "null", "NULL", "Null",
                      "undefined", "Undefined", "None", "none"):
            data = _valid(title=title)
            self.assertIn("job_title", validation_failure_reason(data) or "",
                          f"제목 {title!r} 이 거부되지 않음")

    def test_partial_match_title_is_not_rejected(self):
        """완전일치만 거부 → 'Null'/'None' 이 단어로 포함된 정상 제목은 위양성 아님."""
        for title in ("Null Safety Engineer", "None-the-less 개발자", "nullable 처리"):
            self.assertIsNone(validation_failure_reason(_valid(title=title)),
                              f"정상 제목 {title!r} 이 잘못 거부됨")

    def test_missing_job_link_and_source_url(self):
        data = {"company_name": "카카오", "job_title": "백엔드", "job_link": "", "source_url": ""}
        self.assertIn("job_link", validation_failure_reason(data) or "")


class _CollectorDouble(BaseCollector):
    """DB 엔진 없이 insert_to_db 흐름만 실행하는 최소 더블.

    BaseCollector.__init__(create_engine)을 우회하고 SessionLocal 을 Mock 으로 둔다.
    DB 접근 메서드(_create_collector_run/_insert_one/_finish_collector_run)는
    테스트에서 patch.object 로 대체한다.
    """

    def __init__(self):
        self.database_url = "postgresql://test/ignored"
        self.SessionLocal = MagicMock()

    def collect(self, target_companies):  # pragma: no cover - 추상 메서드 충족용
        return []

    def parse(self, raw_data):  # pragma: no cover - 추상 메서드 충족용
        return raw_data


class GateIntegrationTest(unittest.TestCase):
    def test_invalid_records_counted_as_failed_and_not_inserted(self):
        collector = _CollectorDouble()
        records = [
            _valid("삼성전자", "백엔드 개발자", "https://x.test/1"),
            {"company_name": "", "job_title": "백엔드", "job_link": "https://x.test/2"},  # 무효
            {"company_name": "네이버", "job_title": "(제목 없음)", "job_link": "https://x.test/3"},  # 무효
            _valid("카카오", "프론트엔드 개발자", "https://x.test/4"),
        ]

        with patch.object(_CollectorDouble, "_create_collector_run", return_value=1), \
             patch.object(_CollectorDouble, "_insert_one") as mock_insert, \
             patch.object(_CollectorDouble, "_finish_collector_run") as mock_finish:
            inserted = collector.insert_to_db(records)

        # 유효 2건만 적재되고 _insert_one 도 2회만 호출(무효는 DB 진입 전 거부).
        self.assertEqual(inserted, 2)
        self.assertEqual(mock_insert.call_count, 2)

        # _finish_collector_run(db, run_id, status, inserted, skipped, failed, ...)
        args = mock_finish.call_args.args
        status, ins, skip, fail = args[2], args[3], args[4], args[5]
        self.assertEqual((ins, skip, fail), (2, 0, 2))   # 무효 2건 = failed
        self.assertEqual(status, "partial")              # usable 있고 failed>0


class ParseRegressionTest(unittest.TestCase):
    def test_parse_no_permissive_defaults(self):
        """필수 필드 누락 시 빈문자열/플레이스홀더가 아니라 None 으로 통과(게이트가 거부)."""
        crawler = MultiSourceCrawler.__new__(MultiSourceCrawler)  # __init__(DB) 우회
        out = crawler.parse({})
        self.assertIsNone(out["company_name"])
        self.assertIsNone(out["job_title"])
        self.assertIsNone(out["job_link"])
        self.assertIsNone(out["source_url"])

    def test_parse_preserves_valid_fields(self):
        crawler = MultiSourceCrawler.__new__(MultiSourceCrawler)
        out = crawler.parse(_valid("삼성전자", "백엔드", "https://x.test/9"))
        self.assertEqual(out["company_name"], "삼성전자")
        self.assertEqual(out["job_title"], "백엔드")
        self.assertEqual(out["job_link"], "https://x.test/9")


if __name__ == "__main__":
    unittest.main()
