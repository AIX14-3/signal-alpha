import unittest

from app.core.constants import TR_SECTOR_DAILY
from app.schemas.sector import SectorRef
from app.sector_pipeline import SectorCollectionPipeline
from tests.fakes import FakeKiwoomClient, FakeSectorRepository


def _client() -> FakeKiwoomClient:
    return FakeKiwoomClient(
        {
            TR_SECTOR_DAILY: [
                {
                    "일자": "20260608",
                    "시가": "2700.00",
                    "고가": "2720.00",
                    "저가": "2695.00",
                    "현재가": "2715.00",
                    "거래량": "100000"
                }
            ]
        }
    )


_SECTORS = [
    SectorRef(id=1, kiwoom_code="001", market="KOSPI"),
    SectorRef(id=3, kiwoom_code="004", market="KOSPI")
]


class SectorPipelineTest(unittest.TestCase):
    def test_run_collects_every_active_sector(self) -> None:
        repo = FakeSectorRepository(_SECTORS)
        pipeline = SectorCollectionPipeline(_client(), repo)

        batch = pipeline.run()

        self.assertEqual(batch.status, "success")
        self.assertEqual(batch.inserted_count, 2)  # one row per sector
        self.assertEqual({r.sector_id for r in repo.upserts}, {1, 3})
        self.assertEqual(repo.finished[0]["status"], "success")

    def test_empty_chart_is_skipped(self) -> None:
        repo = FakeSectorRepository(_SECTORS)
        pipeline = SectorCollectionPipeline(FakeKiwoomClient({}), repo)

        batch = pipeline.run()

        self.assertEqual(batch.status, "success")
        self.assertEqual(batch.failed_count, 0)
        self.assertTrue(all(r.status == "skipped" for r in batch.results))
        self.assertEqual(repo.upserts, [])

    def test_collector_error_marks_sector_failed(self) -> None:
        class BoomClient(FakeKiwoomClient):
            def request(self, tr_code, output, **inputs):
                raise RuntimeError("TR rejected")

        repo = FakeSectorRepository(_SECTORS)
        pipeline = SectorCollectionPipeline(BoomClient({}), repo)

        batch = pipeline.run()

        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.failed_count, 2)
        self.assertEqual(repo.finished[0]["status"], "failed")
        self.assertIn("TR rejected", repo.finished[0]["error_message"])


if __name__ == "__main__":
    unittest.main()
