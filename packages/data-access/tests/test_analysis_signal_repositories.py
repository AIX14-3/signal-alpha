import unittest
import json

from signal_alpha_data_access.repositories.analysis import AnalysisRepository
from signal_alpha_data_access.repositories.signals import SignalRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 7, "ticker": "005930", "signal": "neutral"}


class AnalysisAndSignalRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_analysis_result_uses_unique_analysis_conflict(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        row = await repository.upsert_analysis_result(
            stock_id=1,
            analysis_date="2026-06-08",
            run_key="AM",
            source_signal_event_ids=[1, 2],
            base_score=52.5,
            analysis_mode="full",
            version="1.0",
        )

        self.assertEqual(row["id"], 7)
        self.assertIn("ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)", connection.calls[0][1])

    async def test_create_analysis_request_inserts_pending_request(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        row = await repository.create_analysis_request(stock_id=1, analysis_mode="quick")

        self.assertEqual(row["id"], 7)
        self.assertIn("INSERT INTO analysis_requests", connection.calls[0][1])

    async def test_complete_analysis_request_updates_status(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.complete_analysis_request(request_id=7, status="completed")

        self.assertIn("completed_at = NOW()", connection.calls[0][1])

    async def test_upsert_final_signal_uses_final_signal_version_conflict(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.upsert_final_signal(
            stock_id=1,
            analysis_result_id=2,
            signal_date="2026-06-08",
            run_key="AM",
            version="1.0",
            final_score=60,
            confidence=70,
            signal="neutral",
            source_agreement="MEDIUM",
            score_breakdown={"report": 60},
            summary="중립 신호",
        )

        self.assertIn("ON CONFLICT (stock_id, signal_date, run_key, version)", connection.calls[0][1])
        self.assertEqual(json.loads(connection.calls[0][2][10]), {"report": 60})

    async def test_upsert_agent_result_serializes_method_detail_jsonb(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.upsert_agent_result(
            result_id=2,
            stock_id=1,
            debate_method="D-1",
            source_signal_event_ids=[10],
            method_score=50,
            method_signal="neutral",
            method_detail={"source": "DART", "data_status": "partial"},
        )

        self.assertIn("INSERT INTO agent_results", connection.calls[0][1])
        self.assertEqual(json.loads(connection.calls[0][2][6]), {"source": "DART", "data_status": "partial"})

    async def test_get_current_by_ticker_filters_to_current_published_signal(self):
        connection = FakeConnection()
        repository = SignalRepository(connection)

        row = await repository.get_current_by_ticker("005930")

        self.assertEqual(row["id"], 7)
        self.assertIn("final_signals.is_current = TRUE", connection.calls[0][1])
        self.assertIn("final_signals.is_published = TRUE", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("005930",))
