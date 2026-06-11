import unittest

from app.analyzers.price.analyzer import PriceAnalyzer
from app.schemas.evidence import RawEvidence


def make_rows(closes, foreign=None, institution=None):
    foreign = foreign or [None] * len(closes)
    institution = institution or [None] * len(closes)
    return [
        {
            "trade_date": f"2026-03-{index + 1:02d}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000_000,
            "foreign_net": foreign_net,
            "institution_net": institution_net,
        }
        for index, (close, foreign_net, institution_net) in enumerate(
            zip(closes, foreign, institution)
        )
    ]


def make_evidence(rows, stale=False):
    return [
        RawEvidence(
            source="PRICE",
            stock_code="005930",
            title="주가/수급 시계열",
            content="fixture",
            metadata={
                "rows": rows,
                "latest_trade_date": rows[-1]["trade_date"],
                "stale": stale,
            },
        )
    ]


class PriceAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_evidence_is_failed(self):
        result = await PriceAnalyzer().analyze("005930", [])

        self.assertEqual(result.source, "PRICE")
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.data_status, "failed")
        self.assertIn("no_data", result.risk_flags)

    async def test_uptrend_with_buying_flows_is_positive(self):
        closes = [100.0 + index for index in range(70)]
        rows = make_rows(closes, foreign=[100] * 70, institution=[50] * 70)

        result = await PriceAnalyzer().analyze("005930", make_evidence(rows))

        self.assertEqual(result.direction, "positive")
        self.assertGreater(result.score, 0.2)
        self.assertEqual(result.data_status, "ok")
        self.assertTrue(result.evidence_items)
        self.assertEqual(result.evidence_items[0].source_name, "ohlcv_data")

    async def test_stale_data_is_partial_with_flag(self):
        closes = [100.0 + index for index in range(70)]
        rows = make_rows(closes)

        result = await PriceAnalyzer().analyze("005930", make_evidence(rows, stale=True))

        self.assertEqual(result.data_status, "partial")
        self.assertIn("stale_data", result.risk_flags)

    async def test_too_few_sessions_is_partial_unknown(self):
        rows = make_rows([100.0] * 5)

        result = await PriceAnalyzer().analyze("005930", make_evidence(rows))

        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.data_status, "partial")
        self.assertIn("insufficient_history", result.risk_flags)


if __name__ == "__main__":
    unittest.main()
