import unittest

from app.core.constants import (
    TR_DAILY_CHART,
    TR_INVESTOR_FLOW,
    TR_STOCK_BASIC
)
from app.pipeline import PriceCollectionPipeline
from tests.fakes import FakeKiwoomClient, FakeOhlcvRepository


def _client() -> FakeKiwoomClient:
    return FakeKiwoomClient(
        {
            TR_DAILY_CHART: [
                {
                    "일자": "20260608",
                    "시가": "59000",
                    "고가": "59900",
                    "저가": "58000",
                    "현재가": "58900",
                    "거래량": "12345"
                }
            ],
            TR_INVESTOR_FLOW: [
                {
                    "일자": "20260608",
                    "개인투자자": "100",
                    "외국인투자자": "-200",
                    "기관계": "300"
                }
            ],
            TR_STOCK_BASIC: [{"현재가": "58900", "시가총액": "3515000"}]
        }
    )


class PipelineTest(unittest.TestCase):
    def test_run_upserts_rows_and_records_run(self) -> None:
        repo = FakeOhlcvRepository({"005930": 1})
        pipeline = PriceCollectionPipeline(_client(), repo)

        batch = pipeline.run(["005930"])

        self.assertEqual(batch.status, "success")
        self.assertEqual(batch.inserted_count, 1)
        row = repo.upserts[1][0]
        self.assertEqual(row.foreign_net, -200)
        self.assertEqual(row.market_cap, 3515000)
        self.assertEqual(repo.finished[0]["status"], "success")
        self.assertEqual(repo.finished[0]["inserted_count"], 1)

    def test_unknown_ticker_is_skipped_not_failed(self) -> None:
        repo = FakeOhlcvRepository({})  # ticker not seeded
        pipeline = PriceCollectionPipeline(_client(), repo)

        batch = pipeline.run(["999999"])

        self.assertEqual(batch.status, "success")
        self.assertEqual(batch.failed_count, 0)
        self.assertEqual(batch.results[0].status, "skipped")

    def test_collector_error_marks_ticker_failed(self) -> None:
        class BoomClient(FakeKiwoomClient):
            def request(self, tr_code, output, **inputs):
                raise RuntimeError("TR rejected")

        repo = FakeOhlcvRepository({"005930": 1})
        pipeline = PriceCollectionPipeline(BoomClient({}), repo)

        batch = pipeline.run(["005930"])

        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(repo.finished[0]["status"], "failed")
        self.assertIn("TR rejected", repo.finished[0]["error_message"])


if __name__ == "__main__":
    unittest.main()
