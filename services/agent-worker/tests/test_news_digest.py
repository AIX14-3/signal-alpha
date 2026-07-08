"""뉴스 digest 선별기·해시·검증기 순수 단위테스트(LLM/DB 불필요)."""

import unittest
from datetime import datetime, timezone

from app.narrate.base import NarrateError
from app.news import digest


def _art(h, title, summary="", when=None):
    return {"article_hash": h, "title": title, "summary": summary, "published_at": when}


def _dt(day):
    return datetime(2026, 7, day, tzinfo=timezone.utc)


class SelectCandidatesTest(unittest.TestCase):
    def test_relevance_filters_and_orders_recent_first(self):
        arts = [
            _art("a", "삼성전자 3분기 실적 발표", when=_dt(1)),
            _art("b", "무관한 날씨 기사", when=_dt(3)),
            _art("c", "삼성전자 HBM 수주", when=_dt(5)),
        ]
        out = digest.select_candidates(arts, stock_name="삼성전자", ticker="005930", limit=10)
        # 무관 기사 제외, 최신순(c=5 → a=1), 1-based id.
        self.assertEqual([c["article_hash"] for c in out], ["c", "a"])
        self.assertEqual([c["id"] for c in out], [1, 2])

    def test_ticker_match_counts_as_relevant(self):
        out = digest.select_candidates(
            [_art("a", "005930 관련 공시")], stock_name="삼성전자", ticker="005930", limit=5
        )
        self.assertEqual(len(out), 1)

    def test_limit_caps_candidates(self):
        arts = [_art(str(i), f"삼성전자 뉴스 {i}", when=_dt(i)) for i in range(1, 10)]
        out = digest.select_candidates(arts, stock_name="삼성전자", ticker="005930", limit=3)
        self.assertEqual(len(out), 3)

    def test_no_needle_keeps_all(self):
        out = digest.select_candidates(
            [_art("a", "제목")], stock_name=None, ticker=None, limit=5
        )
        self.assertEqual(len(out), 1)


class SourceHashTest(unittest.TestCase):
    def test_order_independent_and_changes_with_set(self):
        c1 = [{"article_hash": "a"}, {"article_hash": "b"}]
        c2 = [{"article_hash": "b"}, {"article_hash": "a"}]
        self.assertEqual(digest.source_hash(c1), digest.source_hash(c2))
        self.assertNotEqual(digest.source_hash(c1), digest.source_hash([{"article_hash": "a"}]))


class ValidateDigestTest(unittest.TestCase):
    ids = {1, 2, 3}

    def test_valid(self):
        text, count = digest.validate_digest(
            {"selected_ids": [1, 3], "digest_text": "HBM 신규 고객 확보 보도."},
            candidate_ids=self.ids,
        )
        self.assertEqual(text, "HBM 신규 고객 확보 보도.")
        self.assertEqual(count, 2)

    def test_empty_text_raises(self):
        with self.assertRaises(NarrateError):
            digest.validate_digest(
                {"selected_ids": [1], "digest_text": ""}, candidate_ids=self.ids
            )

    def test_too_long_raises(self):
        with self.assertRaises(NarrateError):
            digest.validate_digest(
                {"selected_ids": [1], "digest_text": "가" * 121}, candidate_ids=self.ids
            )

    def test_advice_language_raises(self):
        with self.assertRaises(NarrateError):
            digest.validate_digest(
                {"selected_ids": [1], "digest_text": "지금 매수 추천합니다."},
                candidate_ids=self.ids,
            )

    def test_hallucinated_ids_rejected(self):
        with self.assertRaises(NarrateError):
            digest.validate_digest(
                {"selected_ids": [99], "digest_text": "정상 문장."}, candidate_ids=self.ids
            )

    def test_non_dict_raises(self):
        with self.assertRaises(NarrateError):
            digest.validate_digest(["not", "a", "dict"], candidate_ids=self.ids)


if __name__ == "__main__":
    unittest.main()
