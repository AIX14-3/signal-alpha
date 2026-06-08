import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.persistence import AnalysisPersistence
from app.schemas.source_result import EvidenceItem, SourceResult


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.next_id = 200

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.next_id += 1
        return self.next_id


class AnalysisPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_save_source_result_records_normalized_analysis_and_final_signal(self):
        connection = FakeConnection()
        persistence = AnalysisPersistence(connection)

        result = await persistence.save_source_result(
            stock_id=1,
            stock_code="005930",
            source_result=SourceResult(
                source="REPORT",
                stock_code="005930",
                direction="neutral",
                score=55,
                summary="Report evidence is mixed.",
                evidence_items=[
                    EvidenceItem(
                        title="Report published",
                        summary="The report has mixed evidence.",
                        url="https://example.com/report",
                        published_at="2026-06-08T00:00:00+09:00",
                        source_name="Example Securities",
                    )
                ],
            ),
            source_raw_ids=[10],
            run_key="AM",
            version="test",
            publish_final_signal=True,
        )

        self.assertIn("analysis_result_id", result)
        self.assertTrue(any("INSERT INTO source_documents" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO signal_events" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO signal_metrics" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO analysis_results" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO agent_results" in call[1] for call in connection.calls))
        self.assertTrue(any("INSERT INTO final_signals" in call[1] for call in connection.calls))
