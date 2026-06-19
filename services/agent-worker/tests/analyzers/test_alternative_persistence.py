import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "packages" / "data-access"))

from app.analyzers.config import AnalyzerRuntimeConfig
from app.orchestrator.alternative_persistence import AlternativeSignalPersistence
from app.schemas.alternative_signal import AlternativeSignal
from app.schemas.source_result import SourceResult

RUNTIME = AnalyzerRuntimeConfig(version="test", batch_concurrency=4, run_key="BATCH")


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.next_id = 100

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    def find(self, needle):
        return [args for sql, args in self.calls if needle in sql]


def _signal():
    patent = SourceResult("PATENT", "005930", "positive", 0.6, "patent summary")
    datalab = SourceResult("DATALAB", "005930", "negative", -0.6, "datalab summary")
    return AlternativeSignal(
        stock_code="005930",
        direction="mixed",
        score=0.0,
        confidence=0.7,
        source_agreement="MEDIUM",
        score_breakdown={"PATENT": 0.6, "DATALAB": -0.6},
        available_sources=["PATENT", "DATALAB"],
        missing_sources=[],
        risk_flags=[],
        summary="combined",
        per_source={"PATENT": patent, "DATALAB": datalab},
    )


class AlternativeSignalPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = FakeConnection()
        self.persistence = AlternativeSignalPersistence(self.conn, runtime_config=RUNTIME)
        self.result = await self.persistence.save(
            stock_id=1,
            signal=_signal(),
            analysis_date=date(2026, 6, 11),
            publish_final_signal=True,
        )

    async def test_does_not_write_signal_events_or_source_documents(self):
        self.assertEqual(self.conn.find("INSERT INTO signal_events"), [])
        self.assertEqual(self.conn.find("INSERT INTO source_documents"), [])

    async def test_analysis_result_base_score_scaled_to_100(self):
        analysis = self.conn.find("INSERT INTO analysis_results")
        self.assertEqual(len(analysis), 1)
        # args order: request_id, stock_id, date, run_key, ids, base_score, ...
        self.assertEqual(analysis[0][5], 50.0)
        self.assertEqual(analysis[0][4], [])  # empty source_signal_event_ids

    async def test_one_agent_result_per_source_with_scaled_score(self):
        agents = self.conn.find("INSERT INTO agent_results")
        self.assertEqual(len(agents), 2)
        by_method = {args[2]: args for args in agents}  # debate_method
        self.assertEqual(by_method["D-2"][4], 80.0)  # PATENT method_score
        self.assertEqual(by_method["D-3"][4], 20.0)  # DATALAB method_score

    async def test_final_signal_scaled_and_tagged(self):
        finals = self.conn.find("INSERT INTO final_signals")
        self.assertEqual(len(finals), 1)
        args = finals[0]
        self.assertEqual(args[5], 50.0)  # final_score
        self.assertEqual(args[6], 70.0)  # confidence
        self.assertEqual(args[7], "mixed")  # signal
        self.assertEqual(args[8], "MEDIUM")  # source_agreement

    async def test_returns_ids(self):
        self.assertIn("analysis_result_id", self.result)
        self.assertEqual(len(self.result["agent_result_ids"]), 2)
        self.assertIsNotNone(self.result["final_signal_id"])

    async def test_run_key_override_tags_all_three_tables(self):
        # Per-source publishing passes an explicit run_key; it must land on
        # analysis_results AND final_signals (not the runtime default 'BATCH').
        conn = FakeConnection()
        persistence = AlternativeSignalPersistence(conn, runtime_config=RUNTIME)
        await persistence.save(
            stock_id=1,
            signal=_signal(),
            analysis_date=date(2026, 6, 11),
            publish_final_signal=True,
            run_key="PATENT",
        )
        analysis = conn.find("INSERT INTO analysis_results")[0]
        self.assertEqual(analysis[3], "PATENT")  # run_key arg position
        final = conn.find("INSERT INTO final_signals")[0]
        self.assertEqual(final[3], "PATENT")  # run_key arg position

    async def test_jsonb_fields_serialized_once_not_double_encoded(self):
        # Caller passes dict/list (like DART); the repository's _jsonb serializes
        # exactly once. A single json.loads must yield the dict/list — if it were
        # double-encoded it would decode to a string instead.
        agents = self.conn.find("INSERT INTO agent_results")
        by_method = {args[2]: args for args in agents}
        method_detail = json.loads(by_method["D-2"][6])  # $7 → index 6
        self.assertIsInstance(method_detail, dict)
        self.assertEqual(method_detail["source"], "PATENT")

        args = self.conn.find("INSERT INTO final_signals")[0]
        self.assertEqual(json.loads(args[10]), {"PATENT": 80.0, "DATALAB": 20.0})  # score_breakdown
        self.assertIsInstance(json.loads(args[20]), list)  # positive_evidence
        self.assertIsInstance(json.loads(args[21]), list)  # caution_evidence


if __name__ == "__main__":
    unittest.main()
