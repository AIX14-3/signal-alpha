"""observed_date '오늘' 경계 KST 정합성 + per-row override 단위 테스트 (#253).

hiring_raw_details.observed_date 를 DB 서버 tz(CURRENT_DATE)가 아니라 KST 자정 기준으로
고정(_kst_today). UTC 서버에서 KST 00:00~09:00 수집분이 전날로 오분류되는 문제 방지.

추가: backfill 은 과거 공고를 '오늘'이 아니라 실제 게시일(KST)로 적재해야 과거 시계열이
보존된다. _insert_one 이 per-row `observed_date` override 를 받으면 _to_kst_date 로 KST
날짜를 정규화하고, override 가 없으면 _kst_today() 로 기존 라이브 거동을 유지한다.
"""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.collectors.hiring.base_collector import BaseCollector, _kst_today, _to_kst_date


class KstTodayTest(unittest.TestCase):
    def test_returns_date(self):
        self.assertIsInstance(_kst_today(), datetime.date)

    def test_matches_kst_calendar_day(self):
        # _kst_today()는 Asia/Seoul 자정 기준 오늘과 일치해야 한다(서버 로컬/UTC 아님).
        expected = datetime.datetime.now(ZoneInfo("Asia/Seoul")).date()
        self.assertEqual(_kst_today(), expected)

    def test_not_naive_utc_date_when_diverging(self):
        # KST와 UTC의 달력 날짜가 갈리는 시각(UTC 15:00~24:00 = KST 익일 00:00~09:00)에는
        # _kst_today()가 UTC date(date.today 류)보다 하루 앞서야 한다. 그 외 시각엔 동일.
        utc_today = datetime.datetime.now(ZoneInfo("UTC")).date()
        kst = _kst_today()
        self.assertIn((kst - utc_today).days, (0, 1))


class ToKstDateTest(unittest.TestCase):
    def test_date_passthrough(self):
        d = datetime.date(2021, 3, 15)
        self.assertEqual(_to_kst_date(d), d)

    def test_iso_date_string(self):
        self.assertEqual(_to_kst_date("2021-03-15"), datetime.date(2021, 3, 15))

    def test_iso_datetime_string_takes_date(self):
        self.assertEqual(_to_kst_date("2021-03-15T22:10:00"), datetime.date(2021, 3, 15))

    def test_naive_datetime(self):
        dt = datetime.datetime(2021, 3, 15, 9, 0)
        self.assertEqual(_to_kst_date(dt), datetime.date(2021, 3, 15))

    def test_aware_utc_datetime_converts_to_kst_day(self):
        # UTC 2021-03-15 20:00 = KST 2021-03-16 05:00 → KST 날짜는 익일이어야 한다.
        dt = datetime.datetime(2021, 3, 15, 20, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(_to_kst_date(dt), datetime.date(2021, 3, 16))

    def test_messy_string_falls_back_to_date_prefix(self):
        # ISO 파싱 실패 시 앞 10자(YYYY-MM-DD)로 폴백.
        self.assertEqual(_to_kst_date("2021-03-15 (게시)"), datetime.date(2021, 3, 15))


# ── _insert_one per-row observed_date override (DB 없이 패치) ────────────────────
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
            return _FakeResult(fetchone=None)  # baseline 없음 → seasonal None
        if "INSERT INTO raw_documents" in sql:
            return _FakeResult(scalar=4242)  # raw_doc_id RETURNING
        return _FakeResult()  # hiring_raw_details / processing_queue

    def hiring_detail_params(self) -> dict:
        for sql, params in self.calls:
            if "INSERT INTO hiring_raw_details" in sql:
                return params
        raise AssertionError("hiring_raw_details INSERT 가 실행되지 않음")


class _CollectorDouble(BaseCollector):
    """BaseCollector.__init__(create_engine) 우회 — _insert_one 흐름만 실행."""

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


class InsertOneObservedDateTest(unittest.TestCase):
    def _params_for(self, record: dict) -> dict:
        collector = _CollectorDouble()
        db = _FakeDB()
        with patch.object(_CollectorDouble, "_resolve_stock", return_value=(1, "반도체")):
            collector._insert_one(db, record, run_id=1)
        return db.hiring_detail_params()

    def test_override_observed_date_is_used(self):
        # backfill 게시일 override → 그 날짜로 적재(오늘 아님).
        params = self._params_for(_record(posting_date="2021-03-15", observed_date="2021-03-15"))
        self.assertEqual(params["observed_date"], datetime.date(2021, 3, 15))

    def test_override_aware_datetime_normalized_to_kst(self):
        # tz-aware override 도 KST 달력 날짜로 정규화.
        params = self._params_for(
            _record(observed_date=datetime.datetime(2021, 3, 15, 20, 0, tzinfo=ZoneInfo("UTC")))
        )
        self.assertEqual(params["observed_date"], datetime.date(2021, 3, 16))

    def test_no_override_falls_back_to_kst_today(self):
        # 라이브 수집(override 없음) → 기존 거동(_kst_today) 유지.
        params = self._params_for(_record())
        self.assertEqual(params["observed_date"], _kst_today())


if __name__ == "__main__":
    unittest.main()
