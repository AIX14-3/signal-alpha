import unittest

from signal_alpha_data_access.repositories.analysis import AnalysisRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": args[0], "is_published": False, "needs_review": True, "warning_level": "WARNING"}


class AnalysisRepositoryFinalSignalTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_final_signal_by_id_has_no_publish_filter(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.get_final_signal_by_id(final_signal_id=55)

        sql = connection.calls[0][1]
        self.assertIn("FROM final_signals WHERE id = $1", sql)
        self.assertNotIn("is_published", sql)  # 끝단 종합은 미발행/vetoed도 조회
        self.assertEqual(connection.calls[0][2], (55,))

    async def test_update_final_signal_narrative_only_touches_text(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        await repository.update_final_signal_narrative(
            final_signal_id=55, summary="설명", bull_point="p", bear_point="c"
        )

        sql = connection.calls[0][1]
        self.assertIn("UPDATE final_signals", sql)
        self.assertIn("summary = $2", sql)
        # 점수/방향/발행 플래그는 갱신하지 않음
        self.assertNotIn("final_score", sql)
        self.assertNotIn("is_published", sql)
        self.assertNotIn("signal =", sql)
        self.assertEqual(connection.calls[0][2], (55, "설명", "p", "c"))


if __name__ == "__main__":
    unittest.main()
