import unittest
from datetime import date

from app.collectors.daily_chart import DailyChartCollector
from app.collectors.investor_flow import InvestorFlowCollector
from app.collectors.stock_basic import StockBasicCollector
from app.core.constants import (
    TR_DAILY_CHART,
    TR_INVESTOR_FLOW,
    TR_STOCK_BASIC
)
from tests.fakes import FakeKiwoomClient


class DailyChartCollectorTest(unittest.TestCase):
    def test_parses_candles_and_skips_blank_rows(self) -> None:
        client = FakeKiwoomClient(
            {
                TR_DAILY_CHART: [
                    {
                        "일자": "20260608",
                        "시가": "+59000",
                        "고가": "59900",
                        "저가": "-58000",
                        "현재가": "-58900",
                        "거래량": "12,345,678",
                        "거래대금": "725000"
                    },
                    {"일자": ""}  # trailing empty row
                ]
            }
        )

        candles = DailyChartCollector(client).collect("005930")

        self.assertEqual(len(candles), 1)
        candle = candles[0]
        self.assertEqual(candle.trade_date, date(2026, 6, 8))
        self.assertEqual(candle.open, 59000)
        self.assertEqual(candle.close, 58900)  # sign dropped
        self.assertEqual(candle.volume, 12345678)
        self.assertEqual(candle.trading_value, 725000)

    def test_sends_adjusted_price_flag(self) -> None:
        client = FakeKiwoomClient({TR_DAILY_CHART: []})

        DailyChartCollector(client, use_adjusted_price=True).collect("005930")

        _, inputs = client.calls[0]
        self.assertEqual(inputs["수정주가구분"], "1")
        self.assertEqual(inputs["종목코드"], "005930")


class InvestorFlowCollectorTest(unittest.TestCase):
    def test_parses_net_flows_with_sign(self) -> None:
        client = FakeKiwoomClient(
            {
                TR_INVESTOR_FLOW: [
                    {
                        "일자": "20260608",
                        "개인투자자": "+1,000",
                        "외국인투자자": "-2,500",
                        "기관계": "+1,500",
                        "외국인보유율": "53.12"
                    }
                ]
            }
        )

        flows = InvestorFlowCollector(client).collect("005930")

        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].foreign_net, -2500)
        self.assertEqual(flows[0].institution_net, 1500)
        self.assertEqual(flows[0].foreign_holding_pct, 53.12)


class StockBasicCollectorTest(unittest.TestCase):
    def test_parses_snapshot(self) -> None:
        client = FakeKiwoomClient(
            {
                TR_STOCK_BASIC: [
                    {
                        "현재가": "-58900",
                        "시가총액": "3515000",
                        "상장주수": "5969783",
                        "PER": "12.5",
                        "ROE": "8.9"
                    }
                ]
            }
        )

        basic = StockBasicCollector(client).collect("005930")

        self.assertIsNotNone(basic)
        assert basic is not None
        self.assertEqual(basic.close, 58900)
        self.assertEqual(basic.market_cap, 3515000)
        self.assertEqual(basic.per, 12.5)
        self.assertEqual(basic.roe, 8.9)

    def test_returns_none_when_no_record(self) -> None:
        client = FakeKiwoomClient({TR_STOCK_BASIC: []})
        self.assertIsNone(StockBasicCollector(client).collect("005930"))


if __name__ == "__main__":
    unittest.main()
