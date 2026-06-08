import unittest

from signal_alpha_data_access.repositories.scoring import ScoringRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 1

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 1}]


class ScoringRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_quant_score_uses_result_conflict(self):
        connection = FakeConnection()
        repository = ScoringRepository(connection)

        await repository.upsert_quant_score(
            result_id=1,
            stock_id=10,
            score_breakdown={"report": 60},
            overall_score=60,
            available_sources=["REPORT"],
            source_agreement="MEDIUM",
        )

        self.assertIn("ON CONFLICT (result_id)", connection.calls[0][1])

    async def test_upsert_xgb_model_version_uses_model_version_conflict(self):
        connection = FakeConnection()
        repository = ScoringRepository(connection)

        await repository.upsert_xgb_model_version(model_version="v1")

        self.assertIn("ON CONFLICT (model_version)", connection.calls[0][1])

    async def test_record_score_history_requires_reference(self):
        connection = FakeConnection()
        repository = ScoringRepository(connection)

        with self.assertRaises(ValueError):
            await repository.record_score_history(
                stock_id=10,
                signal_date="2026-06-08",
                final_score=60,
            )
