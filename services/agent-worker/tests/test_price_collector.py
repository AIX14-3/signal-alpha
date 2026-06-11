import unittest
from datetime import date
from decimal import Decimal

from app.collectors.price.ohlcv_reader import OhlcvReader


class FakeStockRepository:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def get_by_ticker(self, ticker):
        self.calls.append(ticker)
        return self.row


class FakeMarketDataRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list_recent_ohlcv(self, *, stock_id, limit=120):
        self.calls.append({"stock_id": stock_id, "limit": limit})
        return self.rows


def db_row(trade_date, close=70000, foreign_net=1000):
    return {
        "trade_date": trade_date,
        "open": Decimal(close - 500),
        "high": Decimal(close + 500),
        "low": Decimal(close - 1000),
        "close": Decimal(close),
        "volume": 1_000_000,
        "foreign_net": foreign_net,
        "institution_net": -200,
        "change_pct": Decimal("1.25"),
        "market_cap": 4_000_000,
    }


class OhlcvReaderTest(unittest.IsolatedAsyncioTestCase):
    async def test_collect_maps_db_rows_to_single_price_evidence(self):
        stocks = FakeStockRepository({"id": 7, "name": "삼성전자"})
        market_data = FakeMarketDataRepository(
            [db_row(date(2026, 6, 9)), db_row(date(2026, 6, 10), close=71000)]
        )
        reader = OhlcvReader(stocks=stocks, market_data=market_data, today=date(2026, 6, 11))

        evidence = await reader.collect("005930")

        self.assertEqual(len(evidence), 1)
        item = evidence[0]
        self.assertEqual(item.source, "PRICE")
        self.assertEqual(item.stock_code, "005930")
        self.assertEqual(market_data.calls[0], {"stock_id": 7, "limit": 120})

        rows = item.metadata["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["trade_date"], "2026-06-09")
        self.assertIsInstance(rows[1]["close"], float)
        self.assertEqual(rows[1]["close"], 71000.0)
        self.assertEqual(item.metadata["latest_trade_date"], "2026-06-10")
        self.assertFalse(item.metadata["stale"])
        self.assertEqual(item.metadata["stock_name"], "삼성전자")

    async def test_collect_returns_empty_for_unknown_ticker(self):
        reader = OhlcvReader(
            stocks=FakeStockRepository(None),
            market_data=FakeMarketDataRepository([db_row(date(2026, 6, 10))]),
        )

        self.assertEqual(await reader.collect("000000"), [])

    async def test_collect_returns_empty_when_no_rows(self):
        reader = OhlcvReader(
            stocks=FakeStockRepository({"id": 7, "name": "삼성전자"}),
            market_data=FakeMarketDataRepository([]),
        )

        self.assertEqual(await reader.collect("005930"), [])

    async def test_collect_flags_stale_data(self):
        reader = OhlcvReader(
            stocks=FakeStockRepository({"id": 7, "name": "삼성전자"}),
            market_data=FakeMarketDataRepository([db_row(date(2026, 5, 1))]),
            today=date(2026, 6, 11),
        )

        evidence = await reader.collect("005930")

        self.assertTrue(evidence[0].metadata["stale"])


if __name__ == "__main__":
    unittest.main()
