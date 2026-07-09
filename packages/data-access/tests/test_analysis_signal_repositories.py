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

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"id": 7, "ticker": "005930", "analysis_date": "2026-06-08"}]


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

    async def test_upsert_final_signal_serializes_evidence_jsonb(self):
        # positive_evidence/caution_evidence must go through _jsonb like
        # score_breakdown, so list/dict inputs are serialized (not bound raw,
        # which asyncpg cannot encode for a JSONB column).
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
            score_breakdown={"PATENT": 60},
            summary="중립 신호",
            positive_evidence=["PATENT: 특허 급증"],
            caution_evidence=["소스 간 방향이 엇갈립니다."],
        )

        args = connection.calls[0][2]
        # $21 → index 20 (positive_evidence), $22 → index 21 (caution_evidence)
        self.assertEqual(json.loads(args[20]), ["PATENT: 특허 급증"])
        self.assertEqual(json.loads(args[21]), ["소스 간 방향이 엇갈립니다."])

    async def test_upsert_final_signal_passes_none_evidence_through(self):
        # Omitted evidence stays NULL (not the string "null").
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
            score_breakdown={"PATENT": 60},
            summary="중립 신호",
        )

        args = connection.calls[0][2]
        self.assertIsNone(args[20])
        self.assertIsNone(args[21])

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

    async def test_list_dart_analysis_results_joins_stock_agent_results_and_signal_events(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        rows = await repository.list_dart_analysis_results(
            stock_code="005930",
            analysis_date="2026-06-08",
        )

        self.assertEqual(rows[0]["id"], 7)
        self.assertIn("FROM analysis_results", connection.calls[0][1])
        self.assertIn("INNER JOIN stocks", connection.calls[0][1])
        self.assertIn("agent_results", connection.calls[0][1])
        self.assertIn("signal_events", connection.calls[0][1])
        self.assertIn("ar.run_key LIKE 'DART%'", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("005930", "2026-06-08", 20))

    async def test_list_agent_results_for_aggregation_filters_by_analysis_result_ids(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        rows = await repository.list_agent_results_for_aggregation([100, 101])

        self.assertEqual(rows[0]["id"], 7)
        sql = connection.calls[0][1]
        self.assertIn("FROM agent_results", sql)
        self.assertIn("INNER JOIN analysis_results", sql)
        self.assertIn("agent_results.result_id = ANY($1::BIGINT[])", sql)
        self.assertIn("ORDER BY analysis_results.analysis_date DESC", sql)
        self.assertEqual(connection.calls[0][2], ([100, 101],))

    async def test_list_final_signals_by_stock_filters_aggregated_signals(self):
        connection = FakeConnection()
        repository = AnalysisRepository(connection)

        rows = await repository.list_final_signals_by_stock(
            stock_code="005930",
            signal_date="2026-06-08",
        )

        self.assertEqual(rows[0]["id"], 7)
        sql = connection.calls[0][1]
        self.assertIn("FROM final_signals", sql)
        self.assertIn("INNER JOIN stocks", sql)
        self.assertIn("final_signals.run_key = $5", sql)
        self.assertEqual(connection.calls[0][2], ("005930", "2026-06-08", None, None, "AGGREGATED", 20))

    async def test_get_current_by_ticker_filters_to_current_published_signal(self):
        connection = FakeConnection()
        repository = SignalRepository(connection)

        row = await repository.get_current_by_ticker("005930")

        self.assertEqual(row["id"], 7)
        # 읽기 계약: is_current/is_published 필터와 조인은 api.signals_current view 에 내장.
        self.assertIn("FROM api.signals_current", connection.calls[0][1])
        self.assertIn("ticker = $1", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("005930",))

    async def test_list_current_by_stock_ids_filters_to_current_published_signals(self):
        connection = FakeConnection()
        repository = SignalRepository(connection)

        rows = await repository.list_current_by_stock_ids([1, 2])

        self.assertEqual(rows[0]["id"], 7)
        sql = connection.calls[0][1]
        # 읽기 계약: is_current/is_published 필터는 api.signals_current view 에 내장.
        self.assertIn("FROM api.signals_current", sql)
        self.assertIn("stock_id = ANY", sql)
        self.assertEqual(connection.calls[0][2], ([1, 2],))

    async def test_list_current_by_stock_ids_folds_history_to_latest_row_per_source(self):
        # is_current 는 (stock_id, signal_date, run_key) 유일이라 과거 날짜 행도 딸려 온다.
        # 목록 조회는 (종목, run_key) 최신 1행으로 접어야 호출부가 날짜를 가로질러 평균내지 않는다.
        connection = FakeConnection()
        repository = SignalRepository(connection)

        await repository.list_current_by_stock_ids([1])

        sql = connection.calls[0][1]
        self.assertIn("DISTINCT ON (stock_id, run_key)", sql)
        self.assertIn("signal_date DESC", sql)

    async def test_list_previous_aggregated_picks_the_second_newest_row(self):
        # 변화량(Δ)의 비교 기준. AGGREGATED 만, 종목별 최신 바로 앞 행.
        connection = FakeConnection()
        repository = SignalRepository(connection)

        await repository.list_previous_aggregated_by_stock_ids([1, 2])

        sql = connection.calls[0][1]
        self.assertIn("run_key = 'AGGREGATED'", sql)
        self.assertIn("ROW_NUMBER() OVER", sql)
        self.assertIn("PARTITION BY stock_id", sql)
        self.assertIn("recency_rank = 2", sql)
        self.assertEqual(connection.calls[0][2], ([1, 2],))

    async def test_list_previous_aggregated_short_circuits_on_empty_input(self):
        connection = FakeConnection()
        repository = SignalRepository(connection)

        self.assertEqual(await repository.list_previous_aggregated_by_stock_ids([]), [])
        self.assertEqual(connection.calls, [])

    async def test_list_current_published_limits_stocks_not_rows(self):
        # limit 을 행에 걸면 한 종목의 이력이 창을 다 먹어 다른 종목이 잘려 나간다.
        connection = FakeConnection()
        repository = SignalRepository(connection)

        await repository.list_current_published(30)

        sql = connection.calls[0][1]
        self.assertIn("DISTINCT ON (stock_id, run_key)", sql)
        self.assertIn("recent_stocks", sql)
        # LIMIT 은 종목을 고르는 CTE 안에만 있어야 한다(최종 SELECT 에 걸리면 다시 행 절단).
        self.assertNotIn("LIMIT", sql.rsplit("INNER JOIN recent_stocks", 1)[-1])
        self.assertEqual(connection.calls[0][2], (30,))

    async def test_get_detail_by_id_joins_stock_analysis_agent_results_and_events(self):
        connection = FakeConnection()
        repository = SignalRepository(connection)

        row = await repository.get_detail_by_id(200)

        self.assertEqual(row["id"], 7)
        sql = connection.calls[0][1]
        # 읽기 계약: 조인/JSONB 집계/필터는 api.signal_detail view 에 내장.
        self.assertIn("FROM api.signal_detail", sql)
        self.assertIn("id = $1", sql)
        self.assertEqual(connection.calls[0][2], (200,))
