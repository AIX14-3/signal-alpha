"""목표주가 파싱 회귀 — 실제로 적재를 깨뜨린 값들을 재현한다.

로컬 전 종목 리포트 수집(1,940건) 중 2건이 이 에러로 실패했다:

    DataError: invalid input for query argument $4: 4800000000 (value out of int32 range)

그리고 이미 적재된 값 중 최대가 1,660,002,026 이었다 — "166,000" 뒤의 날짜 ".2026" 을
숫자 정규식이 함께 삼킨 뒤 만원 배수를 곱한 결과다.
"""

from __future__ import annotations

import unittest

from app.collectors.report.parsers.run_parser import (
    MAX_TARGET_PRICE,
    _extract_target_price,
    _parse_price,
)


class ParsePriceTest(unittest.TestCase):
    def test_manwon_multiplier_only_applies_below_ten_thousand(self):
        # "48만원" → 480,000원
        self.assertEqual(_parse_price("48", "만원"), 480_000)
        # 이미 원 단위인 480,000 에 만원을 곱하면 48억 → int32 오버플로(실제 장애).
        self.assertEqual(_parse_price("480,000", "만원"), 480_000)

    def test_out_of_range_values_are_dropped(self):
        self.assertIsNone(_parse_price("0", "원"))
        self.assertIsNone(_parse_price("4800000000", "원"))
        self.assertIsNone(_parse_price("1660002026", None))

    def test_plain_won_price_passes_through(self):
        self.assertEqual(_parse_price("166,000", "원"), 166_000)
        self.assertEqual(_parse_price("71,000", None), 71_000)

    def test_every_accepted_price_fits_int32(self):
        self.assertLess(MAX_TARGET_PRICE, 2**31 - 1)


class ExtractTargetPriceTest(unittest.TestCase):
    def test_date_after_the_price_is_not_glued_on(self):
        text = "목표주가 166,000원 (2026.02.26 기준)"

        self.assertEqual(_extract_target_price(text), 166_000)

    def test_number_regex_stops_at_the_decimal_point(self):
        """DB 에 남은 최대값 1,660,002,026 을 만든 입력.

        예전 `[0-9][0-9,.]*` 는 마침표를 계속 삼켜 "166,000.2026" 을 하나의 숫자로 읽고
        만원 배수를 곱했다(166000.2026 × 10,000 = 1,660,002,026).
        """
        self.assertEqual(_extract_target_price("목표주가 166,000.2026만원"), 166_000)

    def test_number_regex_does_not_swallow_a_full_date(self):
        """숫자 뒤에 날짜가 바로 붙는 경우.

        예전 `[0-9][0-9,.]*` 는 "166,000.2026.02.26" 을 통째로 삼켰고, float() 가 터져
        목표주가를 **아예 못 읽었다**(조용히 None). 이제 숫자에서 끊는다.
        """
        self.assertEqual(_extract_target_price("목표주가 166,000.2026.02.26 발행"), 166_000)

    def test_manwon_notation(self):
        self.assertEqual(_extract_target_price("목표주가를 48만원으로 상향"), 480_000)

    def test_target_price_before_keyword(self):
        self.assertEqual(_extract_target_price("110,000원 목표주가"), 110_000)

    def test_english_notation(self):
        self.assertEqual(_extract_target_price("Target Price: 95,000 KRW"), 95_000)

    def test_garbage_falls_back_to_the_second_pattern_or_none(self):
        # 첫 패턴이 말이 안 되는 값을 뽑으면 다음 패턴을 시도하고, 그마저 없으면 None.
        self.assertIsNone(_extract_target_price("목표주가 0원"))
        self.assertIsNone(_extract_target_price("목표주가에 대한 언급이 없습니다"))


if __name__ == "__main__":
    unittest.main()
