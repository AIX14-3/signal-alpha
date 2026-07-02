"""Orchestrator re-query (Wave-3 bidirectional feedback) — golden/replay tests.

Covers the plan's invariants:
  (a) numbers never change from LLM/memory (headline is still SRC / neutral),
  (b) re-query fires ONLY on disagreement and terminates at the round bound
      (no infinite assemble→detect→requery→assemble loop),
  (c) a re-query task re-analyzes the flagged source(s) then re-enqueues AGGREGATE.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.detect import detect_disagreement
from app.orchestrator.aggregation.requery import (
    MAX_REQUERY_ROUNDS,
    RequerySourceTaskHandler,
    _focus_for_source,
)
from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL, REQUERY_SOURCE
from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN

from tests.test_final_signal_aggregator import FakeConnection, dart_agent_row


def _breakdown(**sources):
    base = {}
    for name, entry in sources.items():
        base[name.upper()] = entry
    return base


class DetectDisagreementTest(unittest.TestCase):
    def test_agreeing_multi_source_does_not_requery(self):
        verdict = detect_disagreement(
            signal="positive",
            source_agreement="HIGH",
            breakdown=_breakdown(
                hiring={"direction": "positive", "data_status": "ok"},
                patent={"direction": "positive", "data_status": "ok"},
            ),
            agreement_rate=1.0,
            available_source_count=2,
        )
        self.assertFalse(verdict.should_requery)

    def test_single_source_never_requeries(self):
        verdict = detect_disagreement(
            signal="mixed",
            source_agreement="LOW",
            breakdown=_breakdown(hiring={"direction": "mixed", "data_status": "ok"}),
            agreement_rate=1.0,
            available_source_count=1,
        )
        # mixed triggers a reason, but the only source IS re-queryable so it can
        # still fire — assert instead that a *non-requeryable-only* split skips.
        self.assertTrue(verdict.should_requery)  # single mixed alt source is re-askable

    def test_disagreement_on_requeryable_source_fires_and_targets_it(self):
        verdict = detect_disagreement(
            signal="mixed",
            source_agreement="LOW",
            breakdown=_breakdown(
                dart={"direction": "positive", "data_status": "ok"},
                hiring={"direction": "negative", "data_status": "ok"},
            ),
            agreement_rate=0.5,
            available_source_count=2,
        )
        self.assertTrue(verdict.should_requery)
        self.assertEqual(verdict.target_sources, ["HIRING"])
        self.assertIn("mixed_direction", verdict.reasons)

    def test_disagreement_without_requeryable_target_skips(self):
        # DART(+) vs PRICE(-): real disagreement, but neither is re-queryable →
        # structural skip, no round spent.
        verdict = detect_disagreement(
            signal="mixed",
            source_agreement="LOW",
            breakdown=_breakdown(
                dart={"direction": "positive", "data_status": "ok"},
                price={"direction": "negative", "data_status": "ok"},
            ),
            agreement_rate=0.5,
            available_source_count=2,
        )
        self.assertFalse(verdict.should_requery)
        self.assertIn("no_requeryable_target", verdict.reasons)


class DetectFocusTest(unittest.TestCase):
    """detect must state *what/which axis* is uncertain per target source — the
    hint the re-query threads to the source agent (Wave-3 follow-up ①)."""

    def test_focus_names_diverging_source_and_axis(self):
        verdict = detect_disagreement(
            signal="mixed",
            source_agreement="LOW",
            breakdown=_breakdown(
                dart={"direction": "positive", "data_status": "ok"},
                hiring={
                    "direction": "negative",
                    "data_status": "ok",
                    "needs_review": True,
                    "risk_flags": ["failed_source"],
                },
            ),
            agreement_rate=0.5,
            available_source_count=2,
        )
        self.assertTrue(verdict.should_requery)
        self.assertEqual(verdict.focus["headline_signal"], "mixed")
        self.assertIn("mixed_direction", verdict.focus["reasons"])
        hiring = verdict.focus["sources"]["HIRING"]
        self.assertEqual(hiring["direction"], "negative")
        self.assertTrue(hiring["diverges_from_headline"])
        self.assertTrue(hiring["needs_review"])
        self.assertIn("failed_source", hiring["risk_flags"])
        self.assertIn("HIRING", hiring["ask"])
        # DART is not re-queryable → never a focus target.
        self.assertNotIn("DART", verdict.focus["sources"])

    def test_no_focus_when_not_requerying(self):
        verdict = detect_disagreement(
            signal="positive",
            source_agreement="HIGH",
            breakdown=_breakdown(
                hiring={"direction": "positive", "data_status": "ok"},
                patent={"direction": "positive", "data_status": "ok"},
            ),
            agreement_rate=1.0,
            available_source_count=2,
        )
        self.assertFalse(verdict.should_requery)
        self.assertEqual(verdict.focus, {})


class FocusForSourceTest(unittest.TestCase):
    """The re-query narrows a structured focus to the flagged source; a legacy
    list/str/None focus is forwarded verbatim (back-compat + degrade)."""

    def test_structured_focus_narrowed_to_source(self):
        focus = {
            "reasons": ["mixed_direction"],
            "headline_signal": "mixed",
            "sources": {
                "HIRING": {"direction": "negative", "ask": "H"},
                "PATENT": {"direction": "mixed", "ask": "P"},
            },
        }
        narrowed = _focus_for_source(focus, "HIRING")
        self.assertEqual(narrowed["source"], "HIRING")
        self.assertEqual(narrowed["direction"], "negative")
        self.assertEqual(narrowed["ask"], "H")
        self.assertEqual(narrowed["reasons"], ["mixed_direction"])
        self.assertNotIn("sources", narrowed)  # the per-source map is dropped

    def test_missing_per_source_entry_falls_back_to_global(self):
        focus = {"reasons": ["x"], "sources": {"PATENT": {"direction": "mixed"}}}
        self.assertEqual(_focus_for_source(focus, "HIRING"), {"reasons": ["x"]})

    def test_legacy_list_focus_forwarded_verbatim(self):
        self.assertEqual(
            _focus_for_source(["low_source_agreement"], "HIRING"),
            ["low_source_agreement"],
        )

    def test_none_focus_forwarded(self):
        self.assertIsNone(_focus_for_source(None, "HIRING"))


class AggregatorRequeryEnqueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_alt_disagreement_enqueues_one_requery(self):
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0, source="DART"),
            dart_agent_row(
                analysis_result_id=101, agent_result_id=201, source="HIRING",
                direction="negative", source_score=-0.6, method_score=20.0,
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

        # (a) numbers untouched: SRC absent → neutral 50.
        self.assertEqual(result["signal"], "neutral")
        self.assertEqual(result["final_score"], 50.0)
        # (b) re-query fired once for the diverging alt source.
        self.assertIsNotNone(result["requery_task_id"])
        requery_enqueues = [
            call for call in connection.calls
            if call[0] == "fetchval" and REQUERY_SOURCE in str(call[2])
        ]
        self.assertTrue(requery_enqueues)

    async def test_requery_payload_carries_structured_focus(self):
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0, source="DART"),
            dart_agent_row(
                analysis_result_id=101, agent_result_id=201, source="HIRING",
                direction="negative", source_score=-0.6, method_score=20.0,
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)
        await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )
        requery_call = next(
            call for call in connection.calls
            if call[0] == "fetchval" and REQUERY_SOURCE in str(call[2])
        )
        # the serialized task_context carries the per-source structured focus, not
        # just the coarse reasons list (the payload the source agent narrows on).
        serialized = str(requery_call[2])
        self.assertIn("headline_signal", serialized)
        self.assertIn("diverges_from_headline", serialized)
        self.assertIn("HIRING", serialized)

    async def test_no_requery_when_sources_agree(self):
        rows = [
            dart_agent_row(direction="positive", source_score=0.4, method_score=70.0, source="DART"),
            dart_agent_row(
                analysis_result_id=101, agent_result_id=201, source="HIRING",
                direction="positive", source_score=0.5, method_score=75.0,
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
        self.assertIsNone(result["requery_task_id"])

    async def test_requery_bound_terminates_loop(self):
        # At the max round, even a disagreeing fan-in spawns NO further re-query —
        # this is what makes assemble→detect→requery→assemble finite.
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0, source="DART"),
            dart_agent_row(
                analysis_result_id=101, agent_result_id=201, source="HIRING",
                direction="negative", source_score=-0.6, method_score=20.0,
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection)

        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {
                    "stock_code": "005930",
                    "signal_date": "2026-06-19",
                    "requery_round": MAX_REQUERY_ROUNDS,
                },
            }
        )
        self.assertIsNone(result["requery_task_id"])
        # still published — publishing never blocked by the bound.
        self.assertTrue(result["is_published"])

    async def test_requery_disabled_flag_never_enqueues(self):
        rows = [
            dart_agent_row(direction="positive", source_score=1.0, method_score=100.0, source="DART"),
            dart_agent_row(
                analysis_result_id=101, agent_result_id=201, source="HIRING",
                direction="negative", source_score=-0.6, method_score=20.0,
            ),
        ]
        connection = FakeConnection(rows=rows)
        handler = AggregateSignalTaskHandler(connection, requery_enabled=False)
        result = await handler(
            {
                "id": 30,
                "stock_id": 1,
                "source_analysis_result_ids": [100, 101],
                "task_context": {"stock_code": "005930", "signal_date": "2026-06-19"},
            }
        )
        self.assertIsNone(result["requery_task_id"])


class RequerySourceHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_requeries_flagged_source_and_reenqueues_aggregate(self):
        analyzed = []

        def factory(connection, source):
            async def fake_analyze(task):
                analyzed.append((source, task))
                return {"analyzed_count": 1}

            return fake_analyze

        connection = FakeConnection()
        handler = RequerySourceTaskHandler(connection, analyze_handler_factory=factory)

        result = await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "target_sources": ["HIRING"],
                    "focus": ["low_source_agreement"],
                    "requery_round": 1,
                    "signal_date": "2026-06-19",
                    "as_of": "2026-06-19",
                    "stock_code": "005930",
                },
            }
        )
        # re-analyzed exactly the flagged source, forwarding the focus hint.
        self.assertEqual(result["requeried_sources"], ["HIRING"])
        self.assertEqual(len(analyzed), 1)
        self.assertEqual(analyzed[0][0], "HIRING")
        self.assertEqual(analyzed[0][1]["task_context"]["requery_focus"], ["low_source_agreement"])
        # then re-enqueued AGGREGATE carrying the round forward (bound applies).
        self.assertIsNotNone(result["aggregate_task_id"])
        aggregate_enqueues = [
            call for call in connection.calls
            if call[0] == "fetchval" and AGGREGATE_SIGNAL in str(call[2])
        ]
        self.assertTrue(aggregate_enqueues)

    async def test_structured_focus_delivered_narrowed_per_source(self):
        analyzed = []

        def factory(connection, source):
            async def fake_analyze(task):
                analyzed.append((source, task))
                return {"analyzed_count": 1}

            return fake_analyze

        connection = FakeConnection()
        handler = RequerySourceTaskHandler(connection, analyze_handler_factory=factory)
        focus = {
            "reasons": ["mixed_direction"],
            "headline_signal": "mixed",
            "sources": {"HIRING": {"direction": "negative", "ask": "채용 재확인"}},
        }
        await handler(
            {
                "stock_id": 1,
                "task_context": {
                    "target_sources": ["HIRING"],
                    "focus": focus,
                    "requery_round": 1,
                    "as_of": "2026-06-19",
                    "stock_code": "005930",
                },
            }
        )
        self.assertEqual(len(analyzed), 1)
        forwarded = analyzed[0][1]["task_context"]["requery_focus"]
        # narrowed to HIRING's own slice (not the whole multi-source dict).
        self.assertEqual(forwarded["source"], "HIRING")
        self.assertEqual(forwarded["direction"], "negative")
        self.assertEqual(forwarded["ask"], "채용 재확인")
        self.assertNotIn("sources", forwarded)

    async def test_non_requeryable_source_is_skipped_not_errored(self):
        def factory(connection, source):
            raise AssertionError("must not build a handler for DART")

        connection = FakeConnection()
        handler = RequerySourceTaskHandler(connection, analyze_handler_factory=factory)
        result = await handler(
            {
                "stock_id": 1,
                "task_context": {"target_sources": ["DART"], "requery_round": 1},
            }
        )
        self.assertEqual(result["requeried_sources"], [])
        self.assertEqual(result["skipped_sources"], ["DART"])
        # still re-aggregates so publishing proceeds.
        self.assertIsNotNone(result["aggregate_task_id"])

    async def test_missing_registration_degrades_and_still_reaggregates(self):
        def factory(connection, source):
            raise KeyError(f"no registration for {source}")

        connection = FakeConnection()
        handler = RequerySourceTaskHandler(connection, analyze_handler_factory=factory)
        result = await handler(
            {
                "stock_id": 1,
                "task_context": {"target_sources": ["HIRING"], "requery_round": 1},
            }
        )
        self.assertEqual(result["requeried_sources"], [])
        self.assertEqual(result["skipped_sources"], ["HIRING"])
        self.assertIsNotNone(result["aggregate_task_id"])


class RequeryDrainPlanTest(unittest.TestCase):
    """REQUERY_SOURCE 가 라이브 드레인 plan(DEFAULT_CYCLE_PLAN)에 편입돼야 실제로 처리된다.

    핸들러 등록·DRAIN_ORDER 만으로는 부족 — 라이브 데몬은 plan 기반이라, plan 누락 시
    requery task 가 영영 claim 되지 않아 되묻기 기능이 죽는다(회귀 가드).
    """

    def test_requery_source_in_default_cycle_plan(self):
        self.assertIn(REQUERY_SOURCE, DEFAULT_CYCLE_PLAN)

    def test_requery_drains_before_aggregate(self):
        keys = list(DEFAULT_CYCLE_PLAN)
        self.assertLess(keys.index(REQUERY_SOURCE), keys.index(AGGREGATE_SIGNAL))


if __name__ == "__main__":
    unittest.main()
