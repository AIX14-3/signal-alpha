"""closing_date 파서 + _insert_one 주입 단위 테스트.

마감일(closing_date)이 소스마다 raw 형식이 제각각이라(사람인 D-22 / 잡코리아
06/15(월) 마감 / 자소설 ISO / 카카오 ~06/22 / 상시채용 / 다수 회사 None) 수집 시점에
표준 파생 키(closing_date_parsed·display·is_always_open)로 정규화한다.

핵심 안전요구: None·빈값·깨진 값에서 절대 예외를 던지지 않아야 한다(파싱 실패가 그
소스 수집을 깨면 안 됨). 그래서 None/garbage 케이스를 명시적으로 검증한다.
"""

from __future__ import annotations

import datetime
import json
import unittest
from unittest.mock import patch

from app.collectors.hiring.base_collector import BaseCollector
from app.collectors.hiring.closing_date import parse_closing_date


class ParseClosingDateTest(unittest.TestCase):
    OBS = datetime.date(2026, 6, 30)

    def test_saramin_dday(self):
        r = parse_closing_date("D-22", datetime.date(2026, 6, 30))
        self.assertEqual(r["closing_date_parsed"], "2026-07-22")
        self.assertEqual(r["closing_date_display"], "2026-07-22")
        self.assertFalse(r["is_always_open"])

    def test_saramin_dday_zero(self):
        r = parse_closing_date("D-0", datetime.date(2026, 6, 30))
        self.assertEqual(r["closing_date_parsed"], "2026-06-30")

    def test_always_open(self):
        for raw in ("상시채용", "상시", "수시채용", "채용시 마감"):
            r = parse_closing_date(raw, self.OBS)
            self.assertTrue(r["is_always_open"], raw)
            self.assertEqual(r["closing_date_display"], "상시채용", raw)
            self.assertIsNone(r["closing_date_parsed"], raw)

    def test_saramin_tilde_mmdd(self):
        r = parse_closing_date("~06/15", datetime.date(2026, 6, 1))
        self.assertEqual(r["closing_date_parsed"], "2026-06-15")

    def test_jobkorea_noisy_mmdd(self):
        r = parse_closing_date("06/15(월) 마감", datetime.date(2026, 6, 1))
        self.assertEqual(r["closing_date_parsed"], "2026-06-15")

    def test_jobkorea_deadline_text_only(self):
        # 날짜 없는 "마감" 텍스트 → parsed None, 원본 display 보존, 예외 없음.
        r = parse_closing_date("마감", datetime.date(2026, 6, 1))
        self.assertIsNone(r["closing_date_parsed"])
        self.assertEqual(r["closing_date_display"], "마감")
        self.assertFalse(r["is_always_open"])

    def test_jasoseol_iso_datetime(self):
        r = parse_closing_date("2026-07-05T23:59:00.000+09:00", self.OBS)
        self.assertEqual(r["closing_date_parsed"], "2026-07-05")
        self.assertEqual(r["closing_date_display"], "2026-07-05")

    def test_kakao_tilde_mmdd(self):
        r = parse_closing_date("~06/22", datetime.date(2026, 6, 1))
        self.assertEqual(r["closing_date_parsed"], "2026-06-22")

    def test_naver_hyundai_iso_date(self):
        r = parse_closing_date("2026-06-22", self.OBS)
        self.assertEqual(r["closing_date_parsed"], "2026-06-22")

    def test_none_is_safe(self):
        # 삼성·SK하이닉스·크래프톤·기아 등 목록에 마감일 없는 소스 → None.
        r = parse_closing_date(None, self.OBS)
        self.assertEqual(
            r,
            {"closing_date_parsed": None, "closing_date_display": None, "is_always_open": False},
        )

    def test_empty_string_is_safe(self):
        for raw in ("", "   "):
            r = parse_closing_date(raw, self.OBS)
            self.assertIsNone(r["closing_date_parsed"], repr(raw))
            self.assertIsNone(r["closing_date_display"], repr(raw))
            self.assertFalse(r["is_always_open"], repr(raw))

    def test_year_rollover_up(self):
        # 마감 MM/DD 가 관측월보다 앞서면(연말 경계) 다음 해.
        r = parse_closing_date("01/05", datetime.date(2026, 12, 20))
        self.assertEqual(r["closing_date_parsed"], "2027-01-05")

    def test_year_rollover_same(self):
        r = parse_closing_date("12/30", datetime.date(2026, 1, 10))
        self.assertEqual(r["closing_date_parsed"], "2026-12-30")

    def test_garbage_is_safe(self):
        for raw in ("abc", "99/99", "13/40", "??"):
            r = parse_closing_date(raw, self.OBS)
            self.assertIsNone(r["closing_date_parsed"], raw)
            self.assertEqual(r["closing_date_display"], raw, raw)
            self.assertFalse(r["is_always_open"], raw)

    def test_no_observed_date_falls_back(self):
        # observed_date None 이면 D-n/MM-DD 기준점이 없어 기타 처리(예외 없음).
        r = parse_closing_date("D-5", None)
        self.assertIsNone(r["closing_date_parsed"])

    def test_never_raises_on_weird_types(self):
        # total function 보증: 예상 못한 타입도 폴백.
        for raw in (123, 4.5, ["x"], {"a": 1}):
            r = parse_closing_date(raw, self.OBS)
            self.assertIsInstance(r, dict)
            self.assertIn("closing_date_parsed", r)


# ── _insert_one 주입 통합 테스트 (DB 없이 패치) ────────────────────────────────
class _FakeResult:
    def __init__(self, scalar=None, fetchone=None):
        self._scalar = scalar
        self._fetchone = fetchone

    def scalar(self):
        return self._scalar

    def fetchone(self):
        return self._fetchone


class _FakeNested:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDB:
    """_insert_one 의 execute 호출만 흉내내고 파라미터를 기록하는 최소 더블."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def begin_nested(self):
        return _FakeNested()

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM hiring_baseline" in sql:
            return _FakeResult(fetchone=None)
        if "INSERT INTO raw_documents" in sql:
            return _FakeResult(scalar=4242)
        return _FakeResult()

    def hiring_detail_params(self) -> dict:
        for sql, params in self.calls:
            if "INSERT INTO hiring_raw_details" in sql:
                return params
        raise AssertionError("hiring_raw_details INSERT 가 실행되지 않음")


class _CollectorDouble(BaseCollector):
    def __init__(self):
        self.database_url = "postgresql://test/ignored"

    def collect(self, target_companies):  # pragma: no cover - 추상 충족용
        return []

    def parse(self, raw_data):  # pragma: no cover - 추상 충족용
        return raw_data


def _record(**over) -> dict:
    rec = {"company_name": "삼성전자", "job_title": "백엔드 개발자", "job_link": "https://x.test/1"}
    rec.update(over)
    return rec


class InsertOneClosingDateTest(unittest.TestCase):
    def _extra_for(self, record: dict) -> dict:
        collector = _CollectorDouble()
        db = _FakeDB()
        with patch.object(_CollectorDouble, "_resolve_stock", return_value=(1, "반도체")):
            collector._insert_one(db, record, run_id=1)
        return json.loads(db.hiring_detail_params()["extra_payload"])

    def test_derived_keys_added_and_original_preserved(self):
        # D-n 공고 → 파생 키 채워지고 원본 closing_date 보존.
        extra = self._extra_for(_record(closing_date="D-10", observed_date="2026-06-30"))
        self.assertEqual(extra["closing_date"], "D-10")          # 원본 보존
        self.assertEqual(extra["closing_date_parsed"], "2026-07-10")
        self.assertEqual(extra["closing_date_display"], "2026-07-10")
        self.assertFalse(extra["is_always_open"])

    def test_none_closing_date_does_not_break_insert(self):
        # 마감일 없는 소스(삼성 등) → 수집이 깨지지 않고 파생 키는 안전한 None/False.
        extra = self._extra_for(_record())  # closing_date 부재
        self.assertIsNone(extra["closing_date"])
        self.assertIsNone(extra["closing_date_parsed"])
        self.assertIsNone(extra["closing_date_display"])
        self.assertFalse(extra["is_always_open"])

    def test_always_open_flagged_in_payload(self):
        extra = self._extra_for(_record(closing_date="상시채용"))
        self.assertTrue(extra["is_always_open"])
        self.assertEqual(extra["closing_date_display"], "상시채용")
        self.assertEqual(extra["closing_date"], "상시채용")  # 원본 보존


if __name__ == "__main__":
    unittest.main()
