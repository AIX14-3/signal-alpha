"""
test_hiring_keyword_generator.py

HiringKeywordGenerator 단위 테스트.

설계 원칙:
  - _SHORT_NAME_MAP 이 제거됐으므로 short_name은 DB 주입값처럼 명시적 파라미터로 전달
  - 'Pure 로직'임을 보장: DB 호출 없음, 외부 I/O 없음
  - Naver API 규격(keywords = list[str]) 준수 여부를 명시적으로 검증
"""

import unittest

from app.collectors.hiring.keyword_generator import HiringKeywordGenerator


class TestHiringKeywordGenerator(unittest.TestCase):

    def setUp(self):
        self.gen = HiringKeywordGenerator()

    # ── Case 1: 약칭 주입 시 5개 키워드 ─────────────────────────────────────
    def test_short_name_injected_adds_extra_keyword(self):
        """DB에서 주입된 short_name='삼성' 이 키워드에 추가되는지 검증."""
        group = self.gen.generate_keyword_group("삼성전자", short_name="삼성")

        keywords = group["keywords"]
        self.assertIsInstance(keywords, list)
        self.assertIsInstance(keywords[0], str)   # {"name": ...} 아님
        self.assertIn("삼성전자 채용",    keywords)
        self.assertIn("삼성전자 공채",    keywords)
        self.assertIn("삼성전자 입사",    keywords)
        self.assertIn("삼성전자 채용공고", keywords)
        self.assertIn("삼성 채용",        keywords)  # short name variant
        self.assertEqual(len(keywords), 5)
        self.assertEqual(group["groupName"], "삼성전자_HIRING_TREND")
        self.assertEqual(group["keyword_count"], 5)
        self.assertEqual(group["category"], "tech")   # 기본값

    def test_sk_hynix_short_name_injected(self):
        group = self.gen.generate_keyword_group("SK하이닉스", short_name="하이닉스")

        keywords = group["keywords"]
        self.assertIn("SK하이닉스 채용", keywords)
        self.assertIn("하이닉스 채용",   keywords)
        self.assertEqual(len(keywords), 5)
        self.assertEqual(group["keyword_count"], 5)

    # ── Case 2: 약칭 없음 (None) → 4개 키워드 ───────────────────────────────
    def test_no_short_name_gives_4_keywords(self):
        """short_name=None (DB에 약칭 미등록) → full name 4개만."""
        group = self.gen.generate_keyword_group("카카오")  # short_name 미전달

        keywords = group["keywords"]
        self.assertIn("카카오 채용",    keywords)
        self.assertIn("카카오 공채",    keywords)
        self.assertIn("카카오 입사",    keywords)
        self.assertIn("카카오 채용공고", keywords)
        self.assertEqual(len(keywords), 4)
        self.assertEqual(group["keyword_count"], 4)
        self.assertEqual(group["groupName"], "카카오_HIRING_TREND")

    def test_explicit_none_short_name_gives_4_keywords(self):
        group = self.gen.generate_keyword_group("크래프톤", short_name=None)
        self.assertEqual(len(group["keywords"]), 4)

    # ── Case 3: 약칭 = 기업명과 동일 → 중복 방지 ────────────────────────────
    def test_short_name_same_as_company_name_no_duplicate(self):
        """short_name이 company_name과 동일하면 중복 키워드 추가 안 함."""
        group = self.gen.generate_keyword_group("기아", short_name="기아")
        self.assertEqual(len(group["keywords"]), 4)

    def test_short_name_whitespace_only_ignored(self):
        group = self.gen.generate_keyword_group("NAVER", short_name="   ")
        self.assertEqual(len(group["keywords"]), 4)

    # ── Case 4: category 파라미터 전달 ──────────────────────────────────────
    def test_category_field_propagated(self):
        group = self.gen.generate_keyword_group("카카오", category="인터넷")
        self.assertEqual(group["category"], "인터넷")

    def test_category_and_short_name_together(self):
        group = self.gen.generate_keyword_group(
            "현대자동차", category="자동차", short_name="현대"
        )
        self.assertEqual(group["category"], "자동차")
        self.assertIn("현대 채용", group["keywords"])
        self.assertEqual(len(group["keywords"]), 5)

    # ── Case 5: 여러 기업 DI 주입 ───────────────────────────────────────────
    def test_multiple_companies_with_short_names(self):
        """generate_for_multiple_companies 에 short_name 포함 dict 전달."""
        companies = [
            {"company_name": "삼성전자",  "category": "반도체", "short_name": "삼성"},
            {"company_name": "SK하이닉스", "category": "반도체", "short_name": "하이닉스"},
            {"company_name": "현대자동차", "category": "자동차", "short_name": "현대"},
        ]
        result = self.gen.generate_for_multiple_companies(companies)

        self.assertEqual(len(result), 3)
        self.assertIn("삼성전자",   result)
        self.assertIn("SK하이닉스", result)
        self.assertIn("현대자동차", result)

        # category 주입 확인
        self.assertEqual(result["삼성전자"]["category"], "반도체")

        # short_name 키워드 확인 — 모두 list[str]
        for name, group in result.items():
            self.assertIsInstance(group["keywords"][0], str)
        self.assertIn("현대 채용", result["현대자동차"]["keywords"])

    def test_multiple_companies_without_short_names(self):
        """short_name 없는 dict → 4개 키워드만."""
        companies = [
            {"company_name": "크래프톤", "category": "게임"},
            {"company_name": "기아",     "category": "자동차"},
        ]
        result = self.gen.generate_for_multiple_companies(companies)
        self.assertEqual(result["크래프톤"]["keyword_count"], 4)
        self.assertEqual(result["기아"]["keyword_count"], 4)

    # ── Case 6: company_name 누락·빈값 건너뜀 ───────────────────────────────
    def test_multiple_companies_skips_missing_name(self):
        companies = [
            {"company_name": "카카오"},
            {"company_id": 2},                           # company_name 없음
            {"company_name": ""},                         # 빈 문자열
        ]
        result = self.gen.generate_for_multiple_companies(companies)
        self.assertEqual(len(result), 1)
        self.assertIn("카카오", result)

    def test_multiple_companies_default_category(self):
        companies = [{"company_name": "크래프톤"}]   # category, short_name 키 없음
        result = self.gen.generate_for_multiple_companies(companies)
        self.assertEqual(result["크래프톤"]["category"], "tech")

    # ── Case 7: 빈 문자열 입력 → ValueError ─────────────────────────────────
    def test_empty_company_name_raises(self):
        with self.assertRaises(ValueError):
            self.gen.generate_keyword_group("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            self.gen.generate_keyword_group("   ")

    # ── Naver API 규격: keywords가 순수 list[str] 임을 명시적으로 검증 ────────
    def test_keywords_are_plain_strings_not_dicts(self):
        group = self.gen.generate_keyword_group("NAVER", short_name="네이버")
        for kw in group["keywords"]:
            self.assertIsInstance(kw, str, f"keyword must be str, got {type(kw)}: {kw}")
            self.assertNotIsInstance(kw, dict)

    # ── 15개 전체 기업 구조 검증 (short_name 미전달 → 4개 기본 키워드) ────────
    def test_all_15_companies_produce_valid_groups(self):
        all_companies = [
            "삼성전자", "SK하이닉스", "한미반도체",
            "NAVER", "카카오", "크래프톤",
            "현대자동차", "기아", "HL만도",
            "HYBE", "SM엔터테인먼트", "스튜디오드래곤",
            "삼성바이오로직스", "셀트리온", "유한양행",
        ]
        for name in all_companies:
            with self.subTest(company=name):
                group = self.gen.generate_keyword_group(name)
                self.assertEqual(group["groupName"], f"{name}_HIRING_TREND")
                self.assertIsInstance(group["keywords"], list)
                self.assertEqual(len(group["keywords"]), 4)  # short_name 없음 → 4개
                self.assertEqual(group["keyword_count"], 4)
                for kw in group["keywords"]:
                    self.assertIsInstance(kw, str)


if __name__ == "__main__":
    unittest.main()
