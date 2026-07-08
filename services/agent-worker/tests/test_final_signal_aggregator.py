import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL


class FakeConnection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []
        self.next_id = 700

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        self.next_id += 1
        return self.next_id


def dart_agent_row(
    *,
    analysis_result_id=100,
    agent_result_id=200,
    direction="neutral",
    source_score=0.0,
    method_score=50.0,
    data_status="ok",
    needs_review=False,
    source="DART",
    risk_flags=None,
    report_quant=None,
    data_age_days=0,
):
    risk_flags = risk_flags or []
    return {
        "analysis_result_id": analysis_result_id,
        "stock_id": 1,
        "analysis_date": date(2026, 6, 19),
        "data_age_days": data_age_days,
        "analysis_run_key": "DART_EVENT_501",
        "analysis_mode": "dart_only",
        "analysis_version": "dart-rules-v1",
        "analysis_source_signal_event_ids": [501],
        "agent_result_id": agent_result_id,
        "debate_method": "D-1",
        "agent_source_signal_event_ids": [501],
        "method_score": method_score,
        "method_signal": direction,
        "method_detail": {
            "source": source,
            "source_score": source_score,
            "data_status": data_status,
            "summary": "DART disclosures show a neutral information direction.",
            "risk_flags": risk_flags,
            "needs_review": needs_review,
            "events": [{"id": 501, "title": "Quarterly report"}],
            **({"report_quant": report_quant} if report_quant is not None else {}),
        },
        "reliability_score": 90,
        "evidence_quality": 100,
        "llm_model": None,
        "prompt_ver": "dart-rules-v1",
    }


class AggregateSignalTaskHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_handler_publishes_dart_single_source_final_signal_with_caution(self):
        connection = FakeConnection(rows=[dart_agent_row()])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["final_signal_id"], 702)
        self.assertEqual(result["signal"], "neutral")
        self.assertEqual(result["final_score"], 50.0)
        self.assertEqual(result["source_agreement"], "LOW")
        self.assertEqual(result["consensus_score"], 50.0)
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["needs_review"])
        self.assertTrue(result["is_published"])
        # 발행은 무조건 — AGGREGATE 는 항상 끝단 LLM 종합(SYNTHESIZE)을 인큐하고, SYNTHESIZE 가
        # 곧장 PUBLISH_SIGNALS 를 인큐한다(발행 차단 게이트 폐기). AGGREGATE 는 발행을 직접 인큐하지 않는다.
        self.assertIsNotNone(result["synthesize_task_id"])
        self.assertNotIn("publish_task_id", result)

        analysis_call = next(call for call in connection.calls if "INSERT INTO analysis_results" in call[1])
        self.assertEqual(analysis_call[2][3], "AGGREGATED")
        self.assertEqual(analysis_call[2][5], 50.0)
        self.assertEqual(analysis_call[2][8], "full")
        self.assertEqual(analysis_call[2][11], "final-agg-v1")

        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        args = final_call[2]
        self.assertEqual(args[3], "AGGREGATED")
        self.assertEqual(args[4], "final-agg-v1")
        self.assertEqual(args[5], 50.0)
        self.assertEqual(args[6], 50.0)
        self.assertEqual(args[7], "neutral")
        self.assertEqual(args[8], "LOW")
        self.assertEqual(args[9], "CAUTION")
        self.assertTrue(args[15])
        self.assertTrue(args[17])
        breakdown = json.loads(args[10])
        self.assertEqual(breakdown["DART"]["score"], 0.0)
        self.assertEqual(breakdown["PRICE"]["data_status"], "missing")
        self.assertEqual(breakdown["HIRING"]["data_status"], "missing")
        self.assertEqual(breakdown["PATENT"]["data_status"], "missing")
        self.assertEqual(breakdown["DATALAB"]["data_status"], "missing")
        self.assertEqual(breakdown["REPORT"]["data_status"], "missing")

    async def test_positive_and_negative_sources_publish_mixed_caution_before_score_threshold(self):
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0),
            dart_agent_row(
                analysis_result_id=101,
                agent_result_id=201,
                direction="negative",
                source_score=-0.5,
                method_score=25.0,
                source="HIRING",
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        # SRC 미계산(src=None) → 헤드라인은 평평한 50 이 아니라 결정론 블렌드로 폴백(neutral-50 박멸).
        self.assertEqual(result["signal"], "mixed")
        self.assertEqual(result["final_score"], 62.5)
        # deterministic_* 는 동일 블렌드값(헤드라인과 일치).
        self.assertEqual(result["deterministic_signal"], "mixed")
        self.assertEqual(result["deterministic_score"], 62.5)
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["needs_review"])
        self.assertTrue(result["is_published"])

    async def test_handler_accepts_alternative_single_source_final_signal(self):
        connection = FakeConnection(
            rows=[
                dart_agent_row(
                    analysis_result_id=100,
                    agent_result_id=200,
                    direction="positive",
                    source_score=0.36,
                    method_score=68.0,
                    source="HIRING",
                )
            ]
        )
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-24"},
            }
        )

        # SRC 미계산 → 결정론 블렌드(positive/68)로 폴백(neutral-50 박멸).
        self.assertEqual(result["signal"], "positive")
        self.assertEqual(result["final_score"], 68.0)
        self.assertEqual(result["deterministic_signal"], "positive")
        self.assertEqual(result["deterministic_score"], 68.0)
        self.assertEqual(result["source_agreement"], "LOW")
        self.assertEqual(result["warning_level"], "CAUTION")
        self.assertTrue(result["is_published"])
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["HIRING"]["analysis_result_id"], 100)
        self.assertEqual(breakdown["HIRING"]["score"], 0.36)
        self.assertEqual(breakdown["DART"]["data_status"], "missing")
        self.assertEqual(breakdown["REPORT"]["data_status"], "missing")

    async def test_report_now_affects_score_and_valuation_is_preserved(self):
        valuation = {
            "target_price": 90000,
            "forward_eps_est": 6000,
            "methodology": "PER",
            "applied_multiple": 14.0,
            "implied_multiple": 15.0,
            "needs_review": False,
        }
        report_row = dart_agent_row(
            analysis_result_id=101,
            agent_result_id=201,
            direction="positive",
            source_score=1.0,
            method_score=100.0,
            source="REPORT",
            report_quant={"valuation": valuation},
        )
        report_row["analysis_run_key"] = "REPORT_EVENT_801"
        report_row["analysis_mode"] = "full"
        rows = [
            dart_agent_row(
                analysis_result_id=100,
                agent_result_id=200,
                direction="positive",
                source_score=0.36,
                method_score=68.0,
                source="DART",
            ),
            report_row,
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-24"},
            }
        )

        # REPORT 가 이제 SCORING_SOURCES 에 편입됨: DART(0.36)+REPORT(1.0) 평균 0.68 → 84.
        # (SRC 미계산 → 결정론 블렌드가 헤드라인). valuation 근거는 그대로 보존.
        self.assertEqual(result["signal"], "positive")
        self.assertEqual(result["final_score"], 84.0)
        self.assertEqual(result["deterministic_signal"], "positive")
        self.assertEqual(result["deterministic_score"], 84.0)
        self.assertEqual(result["source_agreement"], "HIGH")
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["DART"]["score"], 0.36)
        self.assertEqual(breakdown["REPORT"]["analysis_result_id"], 101)
        self.assertEqual(breakdown["REPORT"]["score"], 1.0)
        self.assertTrue(breakdown["REPORT"]["contributes_to_score"])  # 이제 점수 기여
        self.assertEqual(breakdown["REPORT"]["valuation"], valuation)
        self.assertEqual(breakdown["PRICE"]["data_status"], "missing")
        self.assertEqual(breakdown["HIRING"]["data_status"], "missing")
        self.assertEqual(breakdown["DATALAB"]["data_status"], "missing")

    async def test_unknown_source_is_excluded_and_records_validation_log(self):
        row = dart_agent_row(source="")
        row["analysis_run_key"] = "BATCH"
        row["analysis_mode"] = "full"
        connection = FakeConnection(rows=[row])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["warning_level"], "WARNING")
        # warning_level 은 표시용 메타로만 남고 발행은 무조건(발행 차단 게이트 폐기).
        self.assertTrue(result["is_published"])
        self.assertTrue(any("INSERT INTO validation_logs" in call[1] for call in connection.calls))
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["DART"]["data_status"], "missing")

    async def test_fanin_blends_all_sources_distinguishing_no_signal_and_missing(self):
        # No source_analysis_result_ids → the handler fans in by (stock, date) and
        # blends every source. PRICE/DATALAB ran but produced no signal; REPORT was
        # never collected. FakeConnection.fetch is SQL-agnostic, so it returns these
        # rows for the fan-in query the same way it would for the legacy id query.
        rows = [
            dart_agent_row(direction="positive", source_score=0.4, method_score=70.0, source="DART"),
            dart_agent_row(
                analysis_result_id=110, agent_result_id=210, source="PRICE",
                direction="neutral", source_score=0.0, method_score=50.0, data_status="no_signal",
            ),
            dart_agent_row(
                analysis_result_id=120, agent_result_id=220, source="HIRING",
                direction="positive", source_score=0.3, method_score=65.0,
            ),
            dart_agent_row(
                analysis_result_id=130, agent_result_id=230, source="DATALAB",
                direction="neutral", source_score=0.0, method_score=50.0, data_status="no_signal",
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 31,
                "stock_id": 1,
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["aggregated_count"], 4)
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["DART"]["data_status"], "ok")
        self.assertEqual(breakdown["PRICE"]["data_status"], "no_signal")
        self.assertEqual(breakdown["REPORT"]["data_status"], "missing")
        # 대체데이터는 묶지 않고 각자 top-level peer 로 분리(coalesce 폐기).
        self.assertEqual(breakdown["HIRING"]["data_status"], "ok")
        self.assertEqual(breakdown["DATALAB"]["data_status"], "no_signal")

    async def test_reused_source_age_surfaces_in_breakdown(self):
        # last-known 재사용 — 직전(5일 전) DART 결과를 유효기간 내 재사용하면 그 나이가
        # score_breakdown 에 노출돼 "최종 업데이트 N일 전" 서술 근거가 된다.
        connection = FakeConnection(
            rows=[dart_agent_row(direction="positive", source_score=0.4, method_score=70.0, data_age_days=5)]
        )
        handler = AggregateSignalTaskHandler(connection)

        await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-24"},
            }
        )

        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertEqual(breakdown["DART"]["data_age_days"], 5)
        # 당일 수집(미재사용) 소스는 0.
        self.assertEqual(breakdown["PRICE"].get("data_age_days"), None)  # missing 소스엔 age 키 없음

    async def test_headline_uses_deterministic_blend(self):
        # 메타러너 폐기 후 헤드라인은 항상 결정론 등가중 블렌드다(학습 융합 경로 없음).
        # DART positive 0.5 → 75.0, deterministic_blend.
        connection = FakeConnection(
            rows=[dart_agent_row(direction="positive", source_score=0.5, method_score=75.0)],
        )
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["signal"], "positive")
        self.assertEqual(result["final_score"], 75.0)
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        meta = json.loads(final_call[2][10])["_meta"]
        self.assertEqual(meta["headline_method"], "deterministic_blend")
        self.assertEqual(meta["scoring_method"], "deterministic_blend")
        self.assertIn("blend_basis", meta)

    async def test_price_contributes_to_headline_score(self):
        # 6-소스 통합 점수의 핵심: 주가(PRICE)가 SCORING_SOURCES 라 점수에 산입된다. 대체데이터가
        # 전부 no_signal 이어도 PRICE 가 방향/점수를 만들어 중립 50 이 아니게 된다(07-08 버그 수정).
        price_row = dart_agent_row(
            direction="positive", source_score=0.6, method_score=80.0, source="PRICE"
        )
        price_row["analysis_run_key"] = "PRICE"
        price_row["analysis_mode"] = "full"
        dart_no_signal = dart_agent_row(
            analysis_result_id=120, agent_result_id=220, source="DART",
            direction="neutral", source_score=0.0, method_score=50.0, data_status="no_signal",
        )
        connection = FakeConnection(rows=[price_row, dart_no_signal])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        # PRICE 단독 산입 → 0.6 → 80.0, positive. 중립 50 아님.
        self.assertEqual(result["signal"], "positive")
        self.assertEqual(result["final_score"], 80.0)
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertTrue(breakdown["PRICE"]["contributes_to_score"])  # PRICE 이제 점수 기여
        self.assertFalse(breakdown["DART"]["contributes_to_score"])  # no_signal 은 제외
        self.assertEqual(breakdown["_meta"]["headline_method"], "deterministic_blend")

    async def test_headline_neutral_empty_only_when_no_scoring_source(self):
        # scoring 소스가 하나도 없을 때만(전부 no_signal/missing) 중립 50. PRICE 는 이제
        # SCORING_SOURCES 라 산입되므로, 이 케이스는 no_signal 소스만 있을 때다.
        dart_no_signal = dart_agent_row(
            direction="neutral", source_score=0.0, method_score=50.0,
            source="DART", data_status="no_signal",
        )
        connection = FakeConnection(rows=[dart_no_signal])
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        self.assertEqual(result["signal"], "neutral")
        self.assertEqual(result["final_score"], 50.0)
        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        self.assertEqual(json.loads(final_call[2][10])["_meta"]["headline_method"], "neutral_empty")

    async def test_dart_feature_only_marked_non_contributing(self):
        # DART 가 no_signal(feature-only)이면 contributes_to_score=False — 방향 드라이버 오인 방지.
        connection = FakeConnection(
            rows=[
                dart_agent_row(
                    source="DART", direction="neutral", source_score=0.0,
                    method_score=50.0, data_status="no_signal",
                ),
                dart_agent_row(
                    analysis_result_id=120, agent_result_id=220, source="HIRING",
                    direction="positive", source_score=0.3, method_score=65.0,
                ),
            ]
        )
        handler = AggregateSignalTaskHandler(connection)

        await handler(
            {
                "id": 31,
                "stock_id": 1,
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )

        final_call = next(call for call in connection.calls if "INSERT INTO final_signals" in call[1])
        breakdown = json.loads(final_call[2][10])
        self.assertFalse(breakdown["DART"]["contributes_to_score"])
        self.assertTrue(breakdown["HIRING"]["contributes_to_score"])
        self.assertFalse(breakdown["PRICE"]["contributes_to_score"])  # missing source

    async def test_queue_handlers_registers_aggregate_signal(self):
        handlers = build_task_handlers(FakeConnection())

        self.assertIn(AGGREGATE_SIGNAL, handlers)


if __name__ == "__main__":
    unittest.main()
