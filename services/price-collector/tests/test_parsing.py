import unittest
from datetime import date

from app.kiwoom.parsing import (
    field,
    parse_date,
    parse_decimal,
    parse_int,
    parse_price
)
from app.schemas.price import (
    DailyCandle,
    InvestorFlow,
    StockBasic,
    build_ohlcv_rows
)


class ParsingTest(unittest.TestCase):
    def test_parse_int_strips_commas_and_sign(self) -> None:
        self.assertEqual(parse_int("+1,234"), 1234)
        self.assertEqual(parse_int("-58,900"), -58900)
        self.assertEqual(parse_int("  "), 0)
        self.assertEqual(parse_int("--"), 0)

    def test_parse_price_drops_direction_sign(self) -> None:
        # Kiwoom encodes a downtick as a leading '-' on the magnitude.
        self.assertEqual(parse_price("-58900"), 58900)
        self.assertEqual(parse_price("+58900"), 58900)

    def test_parse_decimal_handles_blank(self) -> None:
        self.assertEqual(parse_decimal("12.34"), 12.34)
        self.assertIsNone(parse_decimal(""))
        self.assertIsNone(parse_decimal("-"))

    def test_parse_date(self) -> None:
        self.assertEqual(parse_date("20260608"), date(2026, 6, 8))
        self.assertIsNone(parse_date("2026"))

    def test_field_returns_first_present_alias(self) -> None:
        record = {"상장주식수": "5969", "상장주수": ""}
        self.assertEqual(field(record, "상장주수", "상장주식수"), "5969")
        self.assertEqual(field(record, "없음"), "")


class BuildOhlcvRowsTest(unittest.TestCase):
    def _candles(self) -> list[DailyCandle]:
        return [
            DailyCandle(date(2026, 6, 6), 100, 110, 95, 100, 1000),
            DailyCandle(date(2026, 6, 8), 100, 130, 100, 120, 2000)
        ]

    def test_rows_are_sorted_and_change_pct_uses_prev_close(self) -> None:
        rows = build_ohlcv_rows("005930", list(reversed(self._candles())))

        self.assertEqual([r.trade_date.day for r in rows], [6, 8])
        self.assertIsNone(rows[0].change_pct)
        self.assertEqual(rows[1].change_pct, 20.0)  # 100 -> 120

    def test_flows_merge_by_date(self) -> None:
        flows = [InvestorFlow(date(2026, 6, 8), 10, -5, 3)]
        rows = build_ohlcv_rows("005930", self._candles(), flows)

        self.assertIsNone(rows[0].foreign_net)
        self.assertEqual(rows[1].foreign_net, -5)
        self.assertEqual(rows[1].institution_net, 3)

    def test_market_cap_applied_only_to_latest_date(self) -> None:
        basic = StockBasic(ticker="005930", close=120, market_cap=4000000)
        rows = build_ohlcv_rows("005930", self._candles(), basic=basic)

        self.assertIsNone(rows[0].market_cap)
        self.assertEqual(rows[1].market_cap, 4000000)


if __name__ == "__main__":
    unittest.main()
