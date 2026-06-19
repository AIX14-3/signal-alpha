import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.analyzers.datalab.normalize_rules import classify_datalab_observation
from app.analyzers.hiring.normalize_rules import classify_hiring_posting
from app.analyzers.patent.normalize_rules import classify_patent_filing
from app.agents.base import SourceAgentOutput
from app.analyzers.registry import SourceRegistration
from app.orchestrator.alternative.tasks import (
    AlternativeAnalyzeTaskHandler,
    DataLabNormalizeTaskHandler,
    HiringNormalizeTaskHandler,
    PatentNormalizeTaskHandler,
    _as_of_str,
    _from_output,
    _instantiate_known,
    analysis_task_context,
)
from app.schemas.source_result import EvidenceItem, ReportMeta, SourceResult


class FakeConnection:
    """Records SQL calls; fetch returns the configured rows, ids auto-increment."""

    def __init__(self, rows=None, ticker="005930"):
        self.calls = []
        self.next_id = 400
        self.rows = rows if rows is not None else []
        self.ticker = ticker

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        self.next_id += 1
        return {"id": self.next_id}

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        if "FROM processing_queue" in sql:  # dedupe lookup → no existing task
            return None
        if "SELECT ticker FROM stocks" in sql:
            return self.ticker
        self.next_id += 1
        return self.next_id

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"

    def _inserts(self, table):
        return [c for c in self.calls if f"INSERT INTO {table}" in c[1]]

    def _enqueue_inserts(self):
        return [
            c for c in self.calls
            if c[0] == "fetchval" and "INSERT INTO processing_queue" in c[1]
        ]


HIRING_ROW = {
    "raw_document_id": 10,
    "stock_id": 1,
    "source_type": "HIRING",
    "source_name": "카카오",
    "external_id": "https://jobs.example/123",
    "title": "백엔드 엔지니어",
    "source_url": "https://jobs.example/123",
    "published_at": datetime(2026, 6, 8),
    "collected_at": datetime(2026, 6, 8),
    "keyword": "백엔드 엔지니어",
    "job_category": "IT",
    "job_count": 1,
    "previous_job_count": None,
    "change_pct": None,
    "extra_payload": {"signal_strength": 0.7, "tech_stack": ["python"]},
}

PATENT_ROW = {
    "raw_document_id": 20,
    "stock_id": 2,
    "source_type": "PATENT",
    "source_name": "KIPRIS",
    "title": "반도체 패키징 방법",
    "source_url": "https://kipris.example/app/4420260001",
    "published_at": datetime(2026, 6, 1),
    "collected_at": datetime(2026, 6, 1),
    "application_no": "4420260001",
    "patent_title": "반도체 패키징 방법",
    "applicant_name": "삼성전자",
    "application_date": date(2026, 6, 1),
    "tech_category": "Semiconductors",
    "is_new_category": True,
    "extra_payload": {},
}


# One DataLab observation (one datalab_raw_documents row), joined with its detail
# + raw meta + polarity, as returned by list_datalab_details_by_raw_ids.
DATALAB_ROW = {
    "raw_document_id": 30,
    "category_id": 5,
    "keyword": "갤럭시",
    "keyword_group": "삼성 스마트폰",
    "observed_date": date(2026, 6, 10),
    "search_index": 88.5,
    "previous_search_index": 60.0,
    "change_pct": 47.5,
    "period_type": "daily",
    "device": "all",
    "gender": "all",
    "age_group": "all",
    "is_spike": True,
    "extra_payload": {},
    "polarity": "demand",
    "source_name": "NAVER_DATALAB",
    "title": "갤럭시 검색 트렌드",
    "source_url": None,
    "published_at": datetime(2026, 6, 10),
    "collected_at": datetime(2026, 6, 10),
}


class _DataLabConnection(FakeConnection):
    """Routes fetch by SQL: detail-by-raw-ids vs category→stock mapping."""

    def __init__(self, detail_rows, stock_rows):
        super().__init__()
        self._detail_rows = detail_rows
        self._stock_rows = stock_rows

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "FROM datalab_raw_details" in sql:
            return self._detail_rows
        if "FROM datalab_category_stocks" in sql:
            return self._stock_rows
        return []


class NormalizeRulesTest(unittest.TestCase):
    def test_hiring_posting_is_positive_demand_signal(self):
        c = classify_hiring_posting(HIRING_ROW)
        self.assertEqual(c.event_type, "hiring_posting")
        self.assertEqual(c.signal_direction, "positive")
        self.assertEqual(c.impact_level, "high")  # signal_strength 0.7

    def test_hiring_low_impact_without_strength(self):
        c = classify_hiring_posting({**HIRING_ROW, "extra_payload": {}})
        self.assertEqual(c.impact_level, "low")

    def test_patent_new_category_is_medium_impact(self):
        c = classify_patent_filing(PATENT_ROW)
        self.assertEqual(c.event_type, "patent_new_category")
        self.assertEqual(c.impact_level, "medium")

    def test_patent_plain_filing_is_low_impact(self):
        c = classify_patent_filing({**PATENT_ROW, "is_new_category": False})
        self.assertEqual(c.event_type, "patent_filing")
        self.assertEqual(c.impact_level, "low")

    def test_datalab_demand_spike_is_positive_high(self):
        c = classify_datalab_observation(DATALAB_ROW)
        self.assertEqual(c.event_type, "datalab_search_spike")  # is_spike
        self.assertEqual(c.signal_direction, "positive")        # demand + rising
        self.assertEqual(c.impact_level, "high")                # spike

    def test_datalab_risk_polarity_flips_direction(self):
        c = classify_datalab_observation({**DATALAB_ROW, "polarity": "risk", "is_spike": False})
        self.assertEqual(c.event_type, "datalab_search_observation")
        self.assertEqual(c.signal_direction, "negative")  # risk + rising search = bearish
        self.assertEqual(c.impact_level, "medium")         # |47.5| >= 20, no spike

    def test_datalab_flat_change_is_neutral_low(self):
        c = classify_datalab_observation(
            {**DATALAB_ROW, "change_pct": 0, "is_spike": False}
        )
        self.assertEqual(c.signal_direction, "neutral")
        self.assertEqual(c.impact_level, "low")


class AsOfStrTest(unittest.TestCase):
    def test_normalizes_to_yyyy_mm_dd(self):
        self.assertEqual(_as_of_str("2026-06-16T09:30:00"), "2026-06-16")
        self.assertEqual(_as_of_str(date(2026, 6, 16)), "2026-06-16")
        self.assertEqual(_as_of_str(datetime(2026, 6, 16, 9, 30)), "2026-06-16")
        self.assertEqual(_as_of_str("20260616"), "2026-06-16")  # YYYYMMDD digits

    def test_defaults_blank_to_today(self):
        today = date.today().isoformat()
        self.assertEqual(_as_of_str(None), today)
        self.assertEqual(_as_of_str(""), today)

    def test_rejects_unparseable_input(self):
        # Garbage fails fast rather than silently producing a bogus dedupe key.
        with self.assertRaises(ValueError):
            _as_of_str("not-a-date")


class AnalysisTaskContextTest(unittest.TestCase):
    def test_canonical_key_is_as_of_only(self):
        self.assertEqual(analysis_task_context("2026-06-16"), {"as_of": "2026-06-16"})

    def test_carries_no_stock_identity(self):
        # Regression for the cross-producer dedupe bug: the key must be
        # stock-agnostic (no stock_code/ticker) so run_analyzers.py and the
        # normalize handlers build IS-NOT-DISTINCT-FROM-identical contexts.
        ctx = analysis_task_context("2026-06-16")
        self.assertNotIn("stock_code", ctx)
        self.assertNotIn("ticker", ctx)


class EnqueueDedupeContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalize_enqueue_context_has_no_stock_identity(self):
        # The normalize-side producer must use the canonical {"as_of"} key so it
        # dedupes against run_analyzers.py (which also omits stock_code).
        conn = FakeConnection(rows=[HIRING_ROW])
        handler = HiringNormalizeTaskHandler(conn)
        await handler(
            {"id": 1, "stock_id": 1, "source_raw_ids": [10],
             "task_context": {"as_of": "2026-06-16"}}
        )
        serialized_context = conn._enqueue_inserts()[0][2][6]
        self.assertIn('"as_of": "2026-06-16"', serialized_context)
        self.assertNotIn("stock_code", serialized_context)


class HiringNormalizeHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_and_enqueues_analysis(self):
        conn = FakeConnection(rows=[HIRING_ROW])
        handler = HiringNormalizeTaskHandler(conn)

        result = await handler(
            {"id": 1, "stock_id": 1, "source_raw_ids": [10], "task_context": {"as_of": "2026-06-16"}}
        )

        self.assertEqual(result["normalized_count"], 1)
        self.assertTrue(conn._inserts("source_documents"))
        self.assertTrue(conn._inserts("signal_metrics"))
        self.assertTrue(conn._inserts("validation_logs"))
        event_call = conn._inserts("signal_events")[0]
        self.assertEqual(event_call[2][4], "hiring_posting")   # event_type
        self.assertEqual(event_call[2][6], "positive")         # signal_direction

        enqueues = conn._enqueue_inserts()
        self.assertEqual(len(enqueues), 1)
        self.assertEqual(enqueues[0][2][1], "ANALYZE_ALTERNATIVE")  # task_type
        self.assertIn('"as_of": "2026-06-16"', enqueues[0][2][6])   # serialized context

    async def test_dedupes_enqueue_per_stock(self):
        row_b = {**HIRING_ROW, "raw_document_id": 11, "external_id": "https://jobs.example/124"}
        conn = FakeConnection(rows=[HIRING_ROW, row_b])  # same stock_id=1
        handler = HiringNormalizeTaskHandler(conn)

        await handler({"id": 1, "stock_id": 1, "source_raw_ids": [10, 11], "task_context": {}})

        self.assertEqual(len(conn._enqueue_inserts()), 1)  # one task for the stock

    async def test_accepts_postgres_array_literal_raw_ids(self):
        conn = FakeConnection(rows=[HIRING_ROW])
        handler = HiringNormalizeTaskHandler(conn)
        await handler({"id": 1, "stock_id": 1, "source_raw_ids": "{10}", "task_context": "{}"})
        self.assertEqual(conn.calls[0][2], ([10],))


class PatentNormalizeHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_marks_official_high_reliability_and_application_date(self):
        conn = FakeConnection(rows=[PATENT_ROW])
        handler = PatentNormalizeTaskHandler(conn)

        result = await handler(
            {"id": 2, "stock_id": 2, "source_raw_ids": [20], "task_context": {"as_of": "2026-06-16"}}
        )

        self.assertEqual(result["normalized_count"], 1)
        doc_call = conn._inserts("source_documents")[0]
        self.assertEqual(doc_call[2][8], "high")   # reliability_level
        self.assertTrue(doc_call[2][9])            # is_official
        event_call = conn._inserts("signal_events")[0]
        self.assertEqual(event_call[2][4], "patent_new_category")
        self.assertEqual(event_call[2][5], date(2026, 6, 1))  # event_date = application_date
        self.assertEqual(len(conn._enqueue_inserts()), 1)


class DataLabNormalizeHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_fan_out_per_stock_with_external_anchor(self):
        # One observation (raw_document_id=30, category_id=5) → 2 mapped stocks.
        conn = _DataLabConnection(
            detail_rows=[DATALAB_ROW],
            stock_rows=[{"stock_id": 1, "weight": 1.0}, {"stock_id": 3, "weight": 0.5}],
        )
        handler = DataLabNormalizeTaskHandler(conn)

        result = await handler(
            {"id": 3, "stock_id": None, "source_raw_ids": [30],
             "task_context": {"category_id": 5, "as_of": "2026-06-16"}}
        )

        # fan-out: 1 observation × 2 stocks
        self.assertEqual(result["normalized_count"], 2)
        self.assertEqual(len(conn._inserts("source_documents")), 2)
        self.assertEqual(len(conn._inserts("signal_events")), 2)
        self.assertEqual(len(conn._inserts("signal_metrics")), 2)
        self.assertEqual(len(conn._inserts("validation_logs")), 2)

        # external anchor: ('datalab_raw_documents', raw_document_id, stock_id, ...)
        doc_call = conn._inserts("source_documents")[0]
        self.assertEqual(doc_call[2][0], "datalab_raw_documents")  # external_ref_type
        self.assertEqual(doc_call[2][1], 30)                       # external_ref_id
        self.assertIn(doc_call[2][2], (1, 3))                      # stock_id

        # event: demand spike → positive/high, event_date = observed_date
        event_call = conn._inserts("signal_events")[0]
        self.assertEqual(event_call[2][4], "datalab_search_spike")  # event_type
        self.assertEqual(event_call[2][5], date(2026, 6, 10))       # event_date
        self.assertEqual(event_call[2][6], "positive")             # signal_direction
        self.assertEqual(event_call[2][7], "high")                 # impact_level

        # event_hash differs per stock (the fan-out key)
        hashes = {c[2][2] for c in conn._inserts("signal_events")}
        self.assertEqual(len(hashes), 2)

        # metric carries previous_value + change_pct
        metric_call = conn._inserts("signal_metrics")[0]
        self.assertEqual(metric_call[2][1], "datalab_search_index")

        # one deduped ANALYZE_ALTERNATIVE per distinct stock
        enqueues = conn._enqueue_inserts()
        self.assertEqual({e[2][0] for e in enqueues}, {1, 3})
        self.assertTrue(all(e[2][1] == "ANALYZE_ALTERNATIVE" for e in enqueues))

    async def test_same_category_observations_resolve_mapping_once(self):
        # Two observations of the same category (different dates) × 2 stocks = 4
        # normalized rows, but the category→stock mapping is fetched only once.
        row_b = {**DATALAB_ROW, "raw_document_id": 31, "observed_date": date(2026, 6, 11)}
        conn = _DataLabConnection(
            detail_rows=[DATALAB_ROW, row_b],
            stock_rows=[{"stock_id": 1, "weight": 1.0}, {"stock_id": 3, "weight": 0.5}],
        )
        handler = DataLabNormalizeTaskHandler(conn)

        result = await handler(
            {"id": 3, "stock_id": None, "source_raw_ids": [30, 31],
             "task_context": {"category_id": 5, "as_of": "2026-06-16"}}
        )

        self.assertEqual(result["normalized_count"], 4)
        mapping_fetches = [
            c for c in conn.calls
            if c[0] == "fetch" and "FROM datalab_category_stocks" in c[1]
        ]
        self.assertEqual(len(mapping_fetches), 1)  # cached across both observations
        self.assertEqual(len(conn._enqueue_inserts()), 2)  # still deduped per stock

    async def test_unmapped_category_normalizes_nothing(self):
        conn = _DataLabConnection(detail_rows=[DATALAB_ROW], stock_rows=[])
        handler = DataLabNormalizeTaskHandler(conn)

        result = await handler(
            {"id": 3, "stock_id": None, "source_raw_ids": [30],
             "task_context": {"category_id": 5, "as_of": "2026-06-16"}}
        )

        self.assertEqual(result["normalized_count"], 0)
        self.assertFalse(conn._inserts("source_documents"))
        self.assertFalse(conn._enqueue_inserts())

    async def test_no_raw_rows_is_noop(self):
        conn = _DataLabConnection(detail_rows=[], stock_rows=[{"stock_id": 1, "weight": 1.0}])
        handler = DataLabNormalizeTaskHandler(conn)
        result = await handler(
            {"id": 3, "stock_id": None, "source_raw_ids": [], "task_context": {}}
        )
        self.assertEqual(result["normalized_count"], 0)
        self.assertFalse(conn._enqueue_inserts())


class _FakeLoader:
    def __init__(self, source):
        self.source = source

    async def load(self, *, stock_id, stock_code, as_of):
        return []


class _FakeAnalyzer:
    def __init__(self, source, direction, score):
        self.source = source
        self._direction = direction
        self._score = score

    async def analyze(self, stock_code, evidence):
        return SourceResult(
            source=self.source,
            stock_code=stock_code,
            direction=self._direction,
            score=self._score,
            summary=f"{self.source} ok",
            data_status="ok",
        )


def _registration(source, direction, score):
    return SourceRegistration(
        source=source,
        debate_method="D-1" if source == "HIRING" else "D-2",
        analyzer=_FakeAnalyzer(source, direction, score),
        loader_factory=lambda repo, s=source: _FakeLoader(s),
    )


class AlternativeAnalyzeHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_runs_sources_and_persists_signal(self):
        conn = FakeConnection()
        handler = AlternativeAnalyzeTaskHandler(
            conn,
            registrations=[
                _registration("HIRING", "positive", 0.5),
                _registration("PATENT", "positive", 0.3),
            ],
        )

        result = await handler(
            {"id": 4, "stock_id": 1, "task_context": {"as_of": "2026-06-16", "stock_code": "005930"}}
        )

        self.assertEqual(result["analyzed_count"], 2)
        self.assertEqual(sorted(result["available_sources"]), ["HIRING", "PATENT"])
        self.assertTrue(conn._inserts("analysis_results"))
        self.assertTrue(conn._inserts("agent_results"))
        self.assertTrue(conn._inserts("final_signals"))

    async def test_publishes_one_signal_per_source_with_distinct_run_keys(self):
        conn = FakeConnection()
        handler = AlternativeAnalyzeTaskHandler(
            conn,
            registrations=[
                _registration("HIRING", "positive", 0.5),
                _registration("PATENT", "positive", 0.3),
                _registration("DATALAB", "negative", -0.4),
            ],
        )

        result = await handler(
            {"id": 4, "stock_id": 1, "task_context": {"as_of": "2026-06-16", "stock_code": "005930"}}
        )

        # one analysis_results + one final_signals per source (3 each), not a
        # single blended row.
        self.assertEqual(len(conn._inserts("analysis_results")), 3)
        self.assertEqual(len(conn._inserts("final_signals")), 3)

        # final_signals run_key (arg index 3) is the source's own run_key.
        final_run_keys = {c[2][3] for c in conn._inserts("final_signals")}
        self.assertEqual(final_run_keys, {"HIRING", "PATENT", "DATALAB"})

        # return shape exposes per-source rows with their run_key + final_signal_id.
        by_source = {s["source"]: s for s in result["sources"]}
        self.assertEqual(set(by_source), {"HIRING", "PATENT", "DATALAB"})
        self.assertEqual(by_source["DATALAB"]["run_key"], "DATALAB")
        self.assertEqual(by_source["DATALAB"]["direction"], "negative")
        self.assertIsNotNone(by_source["HIRING"]["final_signal_id"])

    async def test_failed_source_still_publishes_its_own_row(self):
        class _BoomAnalyzer:
            source = "DATALAB"

            async def analyze(self, stock_code, evidence):
                raise RuntimeError("boom")

        boom = SourceRegistration(
            source="DATALAB",
            debate_method="D-3",
            analyzer=_BoomAnalyzer(),
            loader_factory=lambda repo: _FakeLoader("DATALAB"),
        )
        conn = FakeConnection()
        handler = AlternativeAnalyzeTaskHandler(
            conn,
            registrations=[_registration("HIRING", "positive", 0.5), boom],
        )

        result = await handler(
            {"id": 4, "stock_id": 1, "task_context": {"as_of": "2026-06-16", "stock_code": "005930"}}
        )

        # Both sources publish a row; the failed one is recorded (unknown) so the
        # gap is visible, not silently dropped.
        self.assertEqual(len(conn._inserts("final_signals")), 2)
        self.assertEqual(result["available_sources"], ["HIRING"])  # DATALAB excluded from available
        datalab = next(s for s in result["sources"] if s["source"] == "DATALAB")
        self.assertEqual(datalab["data_status"], "failed")
        self.assertEqual(datalab["direction"], "unknown")

    async def test_isolates_failing_source(self):
        class _BoomAnalyzer:
            source = "DATALAB"

            async def analyze(self, stock_code, evidence):
                raise RuntimeError("boom")

        boom = SourceRegistration(
            source="DATALAB",
            debate_method="D-3",
            analyzer=_BoomAnalyzer(),
            loader_factory=lambda repo: _FakeLoader("DATALAB"),
        )
        conn = FakeConnection()
        handler = AlternativeAnalyzeTaskHandler(
            conn,
            registrations=[_registration("HIRING", "positive", 0.5), boom],
        )

        result = await handler(
            {"id": 4, "stock_id": 1, "task_context": {"as_of": "2026-06-16", "stock_code": "005930"}}
        )

        self.assertEqual(result["analyzed_count"], 2)
        self.assertEqual(result["available_sources"], ["HIRING"])  # DATALAB failed → excluded


class _RichAnalyzer:
    """Analyzer returning a fully-populated SourceResult (evidence_items,
    report_meta, risk_flags, llm_model) — exercises every field the
    RuleSourceAgent round-trip must preserve."""

    source = "DATALAB"

    async def analyze(self, stock_code, evidence):
        return SourceResult(
            source="DATALAB",
            stock_code=stock_code,
            direction="negative",
            score=-0.37,
            summary="검색 급락",
            evidence_items=[
                EvidenceItem(
                    title="갤럭시",
                    summary="검색 급락 -40%",
                    url="https://datalab.example/1",
                    published_at="2026-06-10",
                    source_name="NAVER_DATALAB",
                )
            ],
            risk_flags=["demand_drop", "watch"],
            data_status="partial",
            report_meta=ReportMeta(
                avg_target=None,
                upside_pct=None,
                target_trend="down",
                conflict_detected=True,
                opinions=[{"firm": "X", "stance": "sell"}],
            ),
            llm_model="gemini-2.5",
        )


class RunSourceWiringTest(unittest.IsolatedAsyncioTestCase):
    async def test_wired_run_source_equals_direct_analyzer(self):
        # Score-invariance: the SourceResult produced via the wired _run_source
        # (RuleSourceAgent → SourceAgentInput/Output → _from_output) must equal
        # what analyzer.analyze() returns directly — field for field.
        analyzer = _RichAnalyzer()
        as_of = date(2026, 6, 16)
        direct = await analyzer.analyze("005930", [])

        registration = SourceRegistration(
            source="DATALAB",
            debate_method="D-3",
            analyzer=analyzer,
            loader_factory=lambda repo: _FakeLoader("DATALAB"),
        )
        handler = AlternativeAnalyzeTaskHandler(conn := FakeConnection(), registrations=[registration])
        wired = await handler._run_source(
            registration, handler._repository_factory(conn), 1, "005930", as_of
        )

        # Dataclass equality compares every field, incl. nested evidence_items /
        # report_meta — so this proves the wiring is fully lossless.
        self.assertEqual(wired, direct)
        self.assertEqual(wired.score, -0.37)
        self.assertEqual(wired.direction, "negative")
        self.assertEqual(wired.risk_flags, ["demand_drop", "watch"])
        self.assertEqual(wired.data_status, "partial")
        self.assertEqual(wired.llm_model, "gemini-2.5")
        self.assertEqual(wired.evidence_items, direct.evidence_items)
        self.assertEqual(wired.report_meta, direct.report_meta)

    async def test_wired_run_source_empty_evidence_and_no_report_meta(self):
        # The minimal (pure-rule) case: no evidence_items, report_meta=None.
        # _to_output omits both keys from method_detail, so _from_output must
        # still round-trip losslessly via the .get(...) / None branches.
        class _BareAnalyzer:
            source = "HIRING"

            async def analyze(self, stock_code, evidence):
                return SourceResult(
                    source="HIRING",
                    stock_code=stock_code,
                    direction="positive",
                    score=0.21,
                    summary="채용 증가",
                )

        analyzer = _BareAnalyzer()
        direct = await analyzer.analyze("005930", [])
        registration = SourceRegistration(
            source="HIRING",
            debate_method="D-1",
            analyzer=analyzer,
            loader_factory=lambda repo: _FakeLoader("HIRING"),
        )
        handler = AlternativeAnalyzeTaskHandler(conn := FakeConnection(), registrations=[registration])
        wired = await handler._run_source(
            registration, handler._repository_factory(conn), 1, "005930", date(2026, 6, 16)
        )

        self.assertEqual(wired, direct)
        self.assertEqual(wired.evidence_items, [])
        self.assertIsNone(wired.report_meta)


class FromOutputRestoreTest(unittest.TestCase):
    """Unit coverage for the _from_output / _instantiate_known restore path —
    resilience to schema drift (L1) and observability of restore bugs (L2)."""

    def test_ignores_unknown_keys_in_method_detail(self):
        # An extra key on a flattened dataclass (e.g. a field added on the
        # producer side) must NOT crash the restore — known fields survive,
        # unknown ones are dropped.
        output = SourceAgentOutput(
            source="DATALAB",
            stock_code="005930",
            direction="negative",
            score=-0.3,
            summary="급락",
            risk_flags=["watch"],
            method_detail={
                "data_status": "ok",
                "evidence_items": [
                    {
                        "title": "갤럭시",
                        "summary": "검색 급락",
                        "url": None,
                        "published_at": None,
                        "source_name": "NAVER_DATALAB",
                        "future_field": "ignored",  # unknown → dropped
                    }
                ],
                "report_meta": {
                    "avg_target": None,
                    "upside_pct": None,
                    "target_trend": "down",
                    "conflict_detected": False,
                    "opinions": [],
                    "extra_meta": 123,  # unknown → dropped
                },
            },
            data_status="ok",
        )

        result = _from_output(output)

        self.assertEqual(len(result.evidence_items), 1)
        self.assertEqual(result.evidence_items[0].title, "갤럭시")
        self.assertEqual(result.evidence_items[0].source_name, "NAVER_DATALAB")
        self.assertFalse(hasattr(result.evidence_items[0], "future_field"))
        self.assertEqual(result.report_meta.target_trend, "down")
        self.assertFalse(hasattr(result.report_meta, "extra_meta"))

    def test_instantiate_known_filters_to_declared_fields(self):
        item = _instantiate_known(
            EvidenceItem,
            {"title": "t", "summary": "s", "bogus": "x"},
        )
        self.assertEqual(item.title, "t")
        self.assertEqual(item.summary, "s")
        self.assertIsNone(item.url)

    def test_restore_failure_is_logged_and_reraised(self):
        # A genuinely broken payload (missing required field) must raise AND
        # leave a log trail — so a wiring bug is not silently laundered into
        # data_status="failed" by the caller's broad except.
        output = SourceAgentOutput(
            source="DATALAB",
            stock_code="005930",
            direction="neutral",
            score=0.0,
            summary="x",
            method_detail={
                "evidence_items": [{"summary": "missing required title"}],
            },
        )

        with self.assertLogs(
            "app.orchestrator.alternative.tasks", level="ERROR"
        ) as logs:
            with self.assertRaises(TypeError):
                _from_output(output)
        self.assertTrue(any("복원 실패" in m for m in logs.output))


class HandlerRegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_task_handlers_registers_alternative_types(self):
        from app.orchestrator.queue.handlers import build_task_handlers
        from app.orchestrator.queue.task_types import (
            ANALYZE_ALTERNATIVE,
            NORMALIZE_DATALAB,
            NORMALIZE_HIRING,
            NORMALIZE_PATENT,
        )

        handlers = build_task_handlers(FakeConnection())
        for task_type in (NORMALIZE_HIRING, NORMALIZE_PATENT, NORMALIZE_DATALAB, ANALYZE_ALTERNATIVE):
            self.assertIn(task_type, handlers)

    async def test_runner_no_longer_skips_alternative_task(self):
        # Regression for #117: an Alt task with a registered handler runs instead
        # of being mark_skipped("No handler registered").
        from app.orchestrator.queue.tasks import QueueTaskRunner
        from app.orchestrator.queue.task_types import NORMALIZE_HIRING

        class _ClaimConnection(FakeConnection):
            async def fetchrow(self, sql, *args):
                if "UPDATE processing_queue" in sql or "next_task" in sql:
                    return {
                        "id": 1,
                        "stock_id": 1,
                        "task_type": NORMALIZE_HIRING,
                        "source_raw_ids": [10],
                        "task_context": {"as_of": "2026-06-16"},
                        "retry_count": 0,
                        "max_retry_count": 3,
                    }
                return await super().fetchrow(sql, *args)

        claim_conn = _ClaimConnection(rows=[HIRING_ROW])
        runner = QueueTaskRunner(claim_conn, {NORMALIZE_HIRING: HiringNormalizeTaskHandler(claim_conn)})
        result = await runner.run_task(NORMALIZE_HIRING)

        self.assertEqual(result["status"], "success")
        self.assertFalse(
            any("status = 'skipped'" in c[1] for c in claim_conn.calls if c[0] == "execute")
        )


if __name__ == "__main__":
    unittest.main()
