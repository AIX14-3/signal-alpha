"""get_target_companies 회귀 가드 — name + short_name 둘 다 반환하는지 (#436).

수집/backfill 사전필터가 insert 게이트(_match_stock_row, name·short_name 매칭)보다 엄격해지면
한글 공고('네이버')가 영문 name('NAVER')만으로는 전량 누락된다(#176). 이 테스트는
get_target_companies 가 short_name 도 반환함을 고정한다(DB 불필요 — create_engine 을 mock).
선례: test_hiring_multi_source_crawler.py 의 sqlalchemy.create_engine 패치.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.collectors.hiring.base_collector import get_target_companies


def _patch_engine(rows):
    """create_engine → mock. `with engine.connect() as conn:` 의 execute().fetchall() 이 rows 반환."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    return patch(
        "app.collectors.hiring.base_collector.create_engine",
        return_value=engine,
    )


class GetTargetCompaniesTest(unittest.TestCase):
    def test_returns_both_name_and_short_name(self):
        # NAVER: 영문 name + 한글 short_name → 사전필터가 둘 다 알아야 한글 공고가 안 누락.
        rows = [("NAVER", "네이버"), ("삼성전자", "삼성")]
        with _patch_engine(rows):
            names = get_target_companies("postgresql://x")
        self.assertIn("NAVER", names)
        self.assertIn("네이버", names)  # ← #436 핵심: short_name 도 포함(없으면 한글 공고 누락)
        self.assertIn("삼성전자", names)
        self.assertIn("삼성", names)

    def test_name_precedes_short_name(self):
        # 순서 보존: name 이 short_name 보다 앞.
        rows = [("NAVER", "네이버")]
        with _patch_engine(rows):
            names = get_target_companies("postgresql://x")
        self.assertLess(names.index("NAVER"), names.index("네이버"))

    def test_blank_or_null_short_name_yields_name_only(self):
        # short_name 이 None/빈문자면 name 만(무해 — falsy 제외).
        rows = [("X", None), ("Y", "")]
        with _patch_engine(rows):
            names = get_target_companies("postgresql://x")
        self.assertEqual(names, ["X", "Y"])

    def test_dedup_when_name_equals_short_name(self):
        # name == short_name 이면 1개만(순서 보존 dedup).
        rows = [("KT", "KT")]
        with _patch_engine(rows):
            names = get_target_companies("postgresql://x")
        self.assertEqual(names, ["KT"])


if __name__ == "__main__":
    unittest.main()
