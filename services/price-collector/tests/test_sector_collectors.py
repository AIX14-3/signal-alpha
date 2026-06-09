import unittest
from datetime import date

from app.collectors.sector_daily_chart import SectorDailyChartCollector
from app.collectors.sector_quote import SectorQuoteCollector
from app.core.constants import TR_SECTOR_DAILY, TR_SECTOR_QUOTE
from app.schemas.sector import SectorDailyCandle, build_sector_ohlcv_rows
from tests.fakes import FakeKiwoomClient


class SectorDailyChartCollectorTest(unittest.TestCase):
    def test_parses_index_points_with_decimals_and_sign(self) -> None:
        client = FakeKiwoomClient(
            {
                TR_SECTOR_DAILY: [
                    {
                        "일자": "20260608",
                        "시가": "2701.55",
                        "고가": "2720.10",
                        "저가": "-2698.00",  # downtick sign on magnitude
                        "현재가": "-2715.42",
                        "거래량": "123,456",
                        "거래대금": "9,870,000"
                    },
                    {"일자": ""}
                ]
            }
        )

        candles = SectorDailyChartCollector(client).collect("004")

        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.trade_date, date(2026, 6, 8))
        self.assertEqual(candle.open, 2701.55)
        self.assertEqual(candle.low, 2698.00)   # sign dropped
        self.assertEqual(candle.close, 2715.42)
        self.assertEqual(candle.volume, 123456)

    def test_sends_sector_code(self) -> None:
        client = FakeKiwoomClient({TR_SECTOR_DAILY: []})

        SectorDailyChartCollector(client).collect("001", base_date="20260608")

        tr_code, inputs = client.calls[0]
        self.assertEqual(tr_code, TR_SECTOR_DAILY)
        self.assertEqual(inputs["업종코드"], "001")
        self.assertEqual(inputs["기준일자"], "20260608")


class SectorQuoteCollectorTest(unittest.TestCase):
    def test_parses_snapshot(self) -> None:
        client = FakeKiwoomClient(
            {
                TR_SECTOR_QUOTE: [
                    {
                        "업종명": "전기전자",
                        "현재가": "2715.42",
                        "전일대비": "-12.30",
                        "등락률": "-0.45",
                        "거래량": "123456"
                    }
                ]
            }
        )

        quote = SectorQuoteCollector(client).collect("004")

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.name, "전기전자")
        self.assertEqual(quote.close, 2715.42)
        self.assertEqual(quote.change_pct, -0.45)

    def test_returns_none_when_empty(self) -> None:
        client = FakeKiwoomClient({TR_SECTOR_QUOTE: []})
        self.assertIsNone(SectorQuoteCollector(client).collect("004"))


class BuildSectorOhlcvRowsTest(unittest.TestCase):
    def test_change_pct_from_prev_close(self) -> None:
        candles = [
            SectorDailyCandle(date(2026, 6, 6), 100.0, 105.0, 99.0, 100.0),
            SectorDailyCandle(date(2026, 6, 8), 100.0, 130.0, 100.0, 110.0)
        ]

        rows = build_sector_ohlcv_rows(3, list(reversed(candles)))

        self.assertEqual([r.trade_date.day for r in rows], [6, 8])
        self.assertIsNone(rows[0].change_pct)
        self.assertEqual(rows[1].change_pct, 10.0)
        self.assertTrue(all(r.sector_id == 3 for r in rows))


if __name__ == "__main__":
    unittest.main()
