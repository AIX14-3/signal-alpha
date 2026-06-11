import unittest

from app.collectors.hiring.keyword_generator import HiringKeywordGenerator


class TestHiringKeywordGenerator(unittest.TestCase):

    def setUp(self):
        self.gen = HiringKeywordGenerator()

    # ── Case 1: 삼성전자 (short name 있음) ──────────────────────────────────
    def test_samsung_has_short_name_variant(self):
        group = self.gen.generate_keyword_group("삼성전자")

        keywords = group["keywords"]          # list[str] — Naver API 규격
        self.assertIsInstance(keywords, list)
        self.assertIsInstance(keywords[0], str)   # {"name": ...} 아님
        self.assertIn("삼성전자 채용",   keywords)
        self.assertIn("삼성전자 공채",   keywords)
        self.assertIn("삼성전자 입사",   keywords)
        self.assertIn("삼성전자 채용공고", keywords)
        self.assertIn("삼성 채용",       keywords)  # short name variant
        self.assertEqual(len(keywords), 5)
        self.assertEqual(group["groupName"], "삼성전자_HIRING_TREND")
        self.assertEqual(group["keyword_count"], 5)
        self.assertEqual(group["category"], "tech")   # 기본값

    # ── Case 2: SK하이닉스 (short name 있음) ────────────────────────────────
    def test_sk_hynix_short_name(self):
        group = self.gen.generate_keyword_group("SK하이닉스")

        keywords = group["keywords"]
        self.assertIn("SK하이닉스 채용", keywords)
        self.assertIn("하이닉스 채용",   keywords)  # short name variant
        self.assertEqual(len(keywords), 5)
        self.assertEqual(group["keyword_count"], 5)

    # ── Case 3: 카카오 (short name 없음) ────────────────────────────────────
    def test_kakao_no_short_name(self):
        group = self.gen.generate_keyword_group("카카오")

        keywords = group["keywords"]
        self.assertIn("카카오 채용",    keywords)
        self.assertIn("카카오 공채",    keywords)
        self.assertIn("카카오 입사",    keywords)
        self.assertIn("카카오 채용공고", keywords)
        self.assertEqual(len(keywords), 4)          # 중복 없이 4개만
        self.assertEqual(group["keyword_count"], 4)
        self.assertEqual(group["groupName"], "카카오_HIRING_TREND")

    # ── Case 3-b: category 파라미터 전달 ────────────────────────────────────
    def test_category_field_propagated(self):
        group = self.gen.generate_keyword_group("카카오", category="인터넷")
        self.assertEqual(group["category"], "인터넷")

    # ── Case 4: 여러 기업 DI 주입 ───────────────────────────────────────────
    def test_multiple_companies_no_db(self):
        companies = [
            {"company_id": 1, "company_name": "삼성전자",  "category": "반도체"},
            {"company_id": 2, "company_name": "SK하이닉스", "category": "반도체"},
            {"company_id": 5, "company_name": "현대자동차", "category": "자동차"},
        ]
        result = self.gen.generate_for_multiple_companies(companies)

        self.assertEqual(len(result), 3)
        self.assertIn("삼성전자",  result)
        self.assertIn("SK하이닉스", result)
        self.assertIn("현대자동차", result)

        # category 주입 확인
        self.assertEqual(result["삼성전자"]["category"], "반도체")

        # 현대 short name 포함 확인
        hyundai_keywords = result["현대자동차"]["keywords"]
        self.assertIsInstance(hyundai_keywords[0], str)   # list[str] 보장
        self.assertIn("현대 채용", hyundai_keywords)

    # ── Case 4-b: company_name 누락 항목은 건너뜀 ──────────────────────────
    def test_multiple_companies_skips_missing_name(self):
        companies = [
            {"company_id": 1, "company_name": "카카오"},
            {"company_id": 2},                           # company_name 없음
            {"company_id": 3, "company_name": ""},        # 빈 문자열
        ]
        result = self.gen.generate_for_multiple_companies(companies)
        self.assertEqual(len(result), 1)
        self.assertIn("카카오", result)

    # ── Case 4-c: category 없으면 기본값 "tech" ─────────────────────────────
    def test_multiple_companies_default_category(self):
        companies = [{"company_name": "크래프톤"}]   # category 키 없음
        result = self.gen.generate_for_multiple_companies(companies)
        self.assertEqual(result["크래프톤"]["category"], "tech")

    # ── Case 5: 빈 문자열 입력 → ValueError ─────────────────────────────────
    def test_empty_company_name_raises(self):
        with self.assertRaises(ValueError):
            self.gen.generate_keyword_group("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            self.gen.generate_keyword_group("   ")

    # ── Naver API 규격: keywords가 순수 list[str] 임을 명시적으로 검증 ────────
    def test_keywords_are_plain_strings_not_dicts(self):
        group = self.gen.generate_keyword_group("NAVER")
        for kw in group["keywords"]:
            self.assertIsInstance(kw, str, f"keyword must be str, got {type(kw)}: {kw}")
            self.assertNotIsInstance(kw, dict)

    # ── 15개 전체 기업 구조 검증 ────────────────────────────────────────────
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
                self.assertGreaterEqual(len(group["keywords"]), 4)
                self.assertEqual(group["keyword_count"], len(group["keywords"]))
                for kw in group["keywords"]:
                    self.assertIsInstance(kw, str)


if __name__ == "__main__":
    unittest.main()
