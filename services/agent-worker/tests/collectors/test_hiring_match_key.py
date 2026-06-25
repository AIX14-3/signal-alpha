"""BaseCollector._match_key 단위 테스트 — stocks 매칭 전용 정규화(끝 괄호 제거, #496).

저장용 _clean_company_name 과 분리된 매칭 키. jasoseol 이 회사명을 부문/지역으로 장식
('삼성물산(건설)'·'한국전력공사(KEPCO)')해 종목명보다 길어지면 _match_stock_row 가 실패하던
문제를 해소한다. 알려진 한계(bare prefix·중첩 괄호)도 동작 박제용으로 명시한다.
"""

from __future__ import annotations

import unittest

from app.collectors.hiring.base_collector import BaseCollector

mk = BaseCollector._match_key


class MatchKeyTest(unittest.TestCase):
    def test_strips_trailing_division_paren(self):
        # 부문/지역 괄호 제거 → 종목명과 exact 매칭 가능해짐
        self.assertEqual(mk("삼성물산(건설)"), "삼성물산")
        self.assertEqual(mk("삼성물산(패션)"), "삼성물산")
        self.assertEqual(mk("한국전력공사(KEPCO)"), "한국전력공사")

    def test_strips_consecutive_trailing_groups(self):
        # 연속된 끝 괄호 그룹 모두 제거
        self.assertEqual(mk("삼성물산(건설)(대학생인턴)"), "삼성물산")

    def test_strips_whitespace_before_paren(self):
        self.assertEqual(mk("삼성물산 (건설)"), "삼성물산")

    def test_no_paren_unchanged(self):
        # 괄호 없는 이름은 그대로(부문 없는 '한국전력공사'도 보존 → short_name 으로 매칭)
        self.assertEqual(mk("카카오"), "카카오")
        self.assertEqual(mk("한국전력공사"), "한국전력공사")
        self.assertEqual(mk("LG에너지솔루션"), "LG에너지솔루션")

    def test_all_paren_falls_back_to_input(self):
        # 제거 후 빈 문자열이면 원문 폴백(매칭 깨짐 방지)
        self.assertEqual(mk("(파싱불가)"), "(파싱불가)")

    # ── 알려진 한계 (정합성 단언이 아니라 동작 박제 목적) ──────────────────────────
    def test_match_key_bare_prefix_known_limitation(self):
        # bare prefix → 끝 괄호 떼면 '삼성' 만 남아 종목 다중매칭(삼성전자/생명/물산...) 가능.
        # jasoseol 실데이터는 풀네임 표기('삼성물산(건설)')라 거의 없음. 한계 기록용.
        self.assertEqual(mk("삼성(반도체)"), "삼성")

    def test_match_key_nested_paren_known_limitation(self):
        # 중첩 괄호는 ``[^)]*`` 가 안쪽 ')' 에서 멈춰 끝 그룹을 못 떼어 **원문 유지**.
        # jasoseol 실데이터에 사실상 없음. 한계 기록용(나중 디버깅 단서).
        self.assertEqual(mk("삼성물산(건설(협력업체))"), "삼성물산(건설(협력업체))")


if __name__ == "__main__":
    unittest.main()
