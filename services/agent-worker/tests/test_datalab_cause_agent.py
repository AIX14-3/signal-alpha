"""DataLab cause agent (협업안 §4): lead/lag prelabel, graph wiring, fallback,
spike gate, and the cause round-trip through the orchestrator/persistence seam.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.agents.base import SourceAgentInput
from app.agents.datalab.agent import DataLabAnalysisAgent
from app.agents.datalab.graph import DataLabAnalysisGraphAgent
from app.agents.datalab.lead_lag import compute_lead_lag
from app.agents.datalab.llm_classifier import CauseVerdict
from app.orchestrator.alternative.tasks import _from_output
from app.orchestrator.alternative_persistence import _method_detail
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 1)
LOOKBACK = 30
# midpoint = AS_OF - 15 = 2026-05-17: dates after split into the "recent" window.


def _search_row(day: str, index: float, *, spike: bool = False, change: float | None = None):
    return {
        "observed_date": day,
        "search_index": index,
        "is_spike": spike,
        "weight": 1.0,
        "polarity": "demand",
        "change_pct": change,
    }


def _evidence(rows, *, as_of=AS_OF, attention_series=None):
    metadata = {"rows": rows, "as_of": as_of.isoformat(), "lookback_days": LOOKBACK}
    if attention_series is not None:
        metadata["attention_series"] = attention_series
    return [
        RawEvidence(
            source="DATALAB",
            stock_code="005930",
            title="검색 트렌드",
            content="",
            published_at=rows[-1]["observed_date"] if rows else None,
            metadata=metadata,
        )
    ]


def _spike_series(as_of=AS_OF, n_prior=30):
    """PIT search series (dict {iso: value}) whose latest point is an abnormal
    spike → attention tier 급증 (z≈20 ≥ z_surge 3.5), so the analyzer sets the
    ``attention_spike`` risk_flag that the cause gate keys on. ``n_prior`` prior
    points alternate 98/102 (mean 100, pstdev 2) to give a non-zero baseline sd."""
    series = {}
    start = as_of - timedelta(days=n_prior)
    for i in range(n_prior):
        series[(start + timedelta(days=i)).isoformat()] = 98.0 if i % 2 == 0 else 102.0
    series[as_of.isoformat()] = 140.0
    return series


def _spiking_rows():
    # 6 demand observations with a rising recent window (feeds the lead/lag
    # prelabel). The cause gate itself keys on the attention layer (see
    # ``_spike_series``), not on these rows.
    return [
        _search_row("2026-05-05", 100),
        _search_row("2026-05-08", 105),
        _search_row("2026-05-12", 110),
        _search_row("2026-05-20", 180, spike=True, change=60),
        _search_row("2026-05-26", 200, spike=True, change=70),
        _search_row("2026-05-30", 210, change=5),
    ]


def _flat_rows():
    # No spikes, flat index → no "search_spike", near-zero score → gate skips.
    return [_search_row(f"2026-05-{d:02d}", 100) for d in (4, 9, 14, 19, 24, 29)]


class FakeClassifier:
    model = "fake-gemini"

    def __init__(self, *, verdict=None, fail=False):
        self._verdict = verdict
        self._fail = fail
        self.calls = []

    async def classify(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("LLM down")
        return self._verdict


def _price_provider(rows):
    async def provider(stock_id, as_of):
        return rows

    return provider


# ---------------------------------------------------------------------------
# lead/lag pure function
# ---------------------------------------------------------------------------
class LeadLagTest(unittest.TestCase):
    def _prices(self, prior_pct, recent_pct):
        # earliest=100, mid=100*(1+prior), latest=mid*(1+recent)
        mid = 100 * (1 + prior_pct)
        latest = mid * (1 + recent_pct)
        return [
            {"trade_date": "2026-05-05", "close": 100.0},
            {"trade_date": "2026-05-15", "close": mid},
            {"trade_date": "2026-05-30", "close": latest},
        ]

    def test_catalyst_when_search_leads_price(self):
        rows = _spiking_rows()  # recent search avg >> prior
        ll = compute_lead_lag(rows, self._prices(0.0, 0.12), as_of=AS_OF, lookback_days=LOOKBACK)
        self.assertEqual(ll.preliminary_cause, "catalyst")

    def test_fomo_when_price_moved_before_search(self):
        rows = _spiking_rows()
        ll = compute_lead_lag(rows, self._prices(0.12, 0.0), as_of=AS_OF, lookback_days=LOOKBACK)
        self.assertEqual(ll.preliminary_cause, "fomo")

    def test_price_led_when_search_flat_but_price_moves(self):
        rows = _flat_rows()  # search momentum ~0
        ll = compute_lead_lag(rows, self._prices(0.0, 0.12), as_of=AS_OF, lookback_days=LOOKBACK)
        self.assertEqual(ll.preliminary_cause, "price_led")

    def test_none_when_price_series_too_short(self):
        rows = _spiking_rows()
        ll = compute_lead_lag(rows, [{"trade_date": "2026-05-30", "close": 100.0}],
                              as_of=AS_OF, lookback_days=LOOKBACK)
        self.assertIsNone(ll.preliminary_cause)
        self.assertEqual(ll.price_points, 1)


# ---------------------------------------------------------------------------
# graph agent
# ---------------------------------------------------------------------------
class DataLabGraphAgentTest(unittest.IsolatedAsyncioTestCase):
    def _input(self, rows, *, attention_series=None):
        return SourceAgentInput(
            source="DATALAB", stock_code="005930", stock_id=1,
            analysis_date=AS_OF, evidence=_evidence(rows, attention_series=attention_series),
        )

    async def test_notable_spike_runs_llm_and_tags_cause(self):
        classifier = FakeClassifier(
            verdict=CauseVerdict(cause="catalyst", rationale="검색이 가격에 선행", confidence=0.8)
        )
        agent = DataLabAnalysisGraphAgent(
            classifier=classifier,
            price_provider=_price_provider([
                {"trade_date": "2026-05-05", "close": 100.0},
                {"trade_date": "2026-05-15", "close": 100.0},
                {"trade_date": "2026-05-30", "close": 112.0},
            ]),
            lookback_days=LOOKBACK,
        )
        out = await agent.analyze(self._input(_spiking_rows(), attention_series=_spike_series()))

        self.assertEqual(out.analysis_source, "llm")
        self.assertEqual(out.llm_model, "fake-gemini")
        self.assertEqual(out.method_detail["cause"], "catalyst")
        self.assertEqual(out.method_detail["cause_source"], "llm")
        self.assertEqual(out.method_detail["graph"], "datalab_cause_v1")
        # Spike gate is a first-class graph branch → classify_cause node ran.
        self.assertEqual(
            out.method_detail["graph_nodes"],
            ["validate_input", "analyze_rules", "classify_cause", "validate_output"],
        )
        self.assertEqual(len(classifier.calls), 1)

    async def test_llm_failure_falls_back_to_prelabel(self):
        rows = _spiking_rows()
        prices = [
            {"trade_date": "2026-05-05", "close": 100.0},
            {"trade_date": "2026-05-15", "close": 112.0},  # prior jump → fomo prelabel
            {"trade_date": "2026-05-30", "close": 112.0},
        ]
        prelabel = compute_lead_lag(rows, prices, as_of=AS_OF, lookback_days=LOOKBACK).preliminary_cause
        self.assertIsNotNone(prelabel)

        classifier = FakeClassifier(fail=True)
        agent = DataLabAnalysisGraphAgent(
            classifier=classifier, price_provider=_price_provider(prices), lookback_days=LOOKBACK
        )
        out = await agent.analyze(self._input(rows, attention_series=_spike_series()))

        self.assertEqual(out.analysis_source, "rules_fallback")
        self.assertEqual(out.method_detail["cause"], prelabel)
        self.assertEqual(out.method_detail["cause_source"], "rules_fallback")
        self.assertIsNotNone(out.llm_error)

    async def test_weak_signal_skips_classifier(self):
        classifier = FakeClassifier(verdict=CauseVerdict("catalyst", "x", 0.5))
        agent = DataLabAnalysisGraphAgent(
            classifier=classifier, price_provider=_price_provider([]), lookback_days=LOOKBACK
        )
        out = await agent.analyze(self._input(_flat_rows()))

        self.assertEqual(out.analysis_source, "rules")
        self.assertNotIn("cause", out.method_detail)
        self.assertEqual(len(classifier.calls), 0)
        # Spike gate routed around classify_cause.
        self.assertEqual(
            out.method_detail["graph_nodes"], ["validate_input", "analyze_rules", "validate_output"]
        )

    async def test_invalid_input_is_failed(self):
        agent = DataLabAnalysisGraphAgent(classifier=FakeClassifier())
        out = await agent.analyze(
            SourceAgentInput(source="DATALAB", stock_code="005930", evidence=[])
        )
        self.assertEqual(out.data_status, "failed")
        self.assertEqual(out.analysis_source, "graph_validation")
        self.assertIn("evidence_required", out.risk_flags)

    async def test_score_unchanged_by_cause(self):
        """Cause is a tag only — score/direction stay the rule values (docs §9)."""
        classifier = FakeClassifier(verdict=CauseVerdict("catalyst", "x", 0.9))
        prices = [
            {"trade_date": "2026-05-05", "close": 100.0},
            {"trade_date": "2026-05-15", "close": 100.0},
            {"trade_date": "2026-05-30", "close": 112.0},
        ]
        agent = DataLabAnalysisGraphAgent(
            classifier=classifier, price_provider=_price_provider(prices), lookback_days=LOOKBACK
        )
        rows = _spiking_rows()
        series = _spike_series()
        with_cause = await agent.analyze(self._input(rows, attention_series=series))
        rules_only = await DataLabAnalysisGraphAgent(lookback_days=LOOKBACK).analyze(
            self._input(rows, attention_series=series)
        )
        self.assertEqual(with_cause.score, rules_only.score)
        self.assertEqual(with_cause.direction, rules_only.direction)

    async def test_attention_preserved_and_lead_lag_feature_added(self):
        """The cause path is output-equivalent to the rules path for every non-cause
        field — the neutral attention axis (flag + evidence) is untouched — and only
        *adds* the deterministic lead_lag feature block beside cause (Wave-3 reads it)."""
        classifier = FakeClassifier(verdict=CauseVerdict("catalyst", "검색 선행", 0.8))
        prices = [
            {"trade_date": "2026-05-05", "close": 100.0},
            {"trade_date": "2026-05-15", "close": 100.0},
            {"trade_date": "2026-05-30", "close": 112.0},
        ]
        series = _spike_series()
        with_cause = await DataLabAnalysisGraphAgent(
            classifier=classifier, price_provider=_price_provider(prices), lookback_days=LOOKBACK
        ).analyze(self._input(_spiking_rows(), attention_series=series))
        rules_only = await DataLabAnalysisGraphAgent(lookback_days=LOOKBACK).analyze(
            self._input(_spiking_rows(), attention_series=series)
        )
        # Neutral attention-spike axis is preserved identically (flag + evidence).
        self.assertIn("attention_spike", rules_only.risk_flags)
        self.assertEqual(with_cause.risk_flags, rules_only.risk_flags)
        self.assertEqual(
            with_cause.method_detail["evidence_items"], rules_only.method_detail["evidence_items"]
        )
        # Deterministic lead_lag feature added beside cause; existing keys intact.
        self.assertIn("cause_lead_lag", with_cause.method_detail)
        self.assertIn("search_momentum_pct", with_cause.method_detail["cause_lead_lag"])
        self.assertNotIn("cause_lead_lag", rules_only.method_detail)


# ---------------------------------------------------------------------------
# cause round-trip through the orchestrator/persistence seam
# ---------------------------------------------------------------------------
class StandaloneAgentTest(unittest.IsolatedAsyncioTestCase):
    """The logic agent runs without langgraph (DART-style split)."""

    async def test_agent_tags_cause_without_graph(self):
        classifier = FakeClassifier(verdict=CauseVerdict("catalyst", "검색 선행", 0.8))
        agent = DataLabAnalysisAgent(
            classifier=classifier,
            price_provider=_price_provider([
                {"trade_date": "2026-05-05", "close": 100.0},
                {"trade_date": "2026-05-15", "close": 100.0},
                {"trade_date": "2026-05-30", "close": 112.0},
            ]),
            lookback_days=LOOKBACK,
        )
        out = await agent.analyze(SourceAgentInput(
            source="DATALAB", stock_code="005930", stock_id=1,
            analysis_date=AS_OF,
            evidence=_evidence(_spiking_rows(), attention_series=_spike_series())))
        self.assertEqual(out.analysis_source, "llm")
        self.assertEqual(out.method_detail["cause"], "catalyst")
        self.assertNotIn("graph", out.method_detail)  # no graph wrapper involved


class CauseRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_from_output_and_method_detail_carry_cause(self):
        classifier = FakeClassifier(verdict=CauseVerdict("catalyst", "검색 선행", 0.8))
        prices = [
            {"trade_date": "2026-05-05", "close": 100.0},
            {"trade_date": "2026-05-15", "close": 100.0},
            {"trade_date": "2026-05-30", "close": 112.0},
        ]
        agent = DataLabAnalysisGraphAgent(
            classifier=classifier, price_provider=_price_provider(prices), lookback_days=LOOKBACK
        )
        out = await agent.analyze(
            SourceAgentInput(source="DATALAB", stock_code="005930", stock_id=1,
                             analysis_date=AS_OF,
                             evidence=_evidence(_spiking_rows(), attention_series=_spike_series()))
        )
        # _from_output (orchestrator) lifts cause off method_detail onto SourceResult,
        result = _from_output(out)
        self.assertEqual(result.cause, "catalyst")
        self.assertEqual(result.cause_source, "llm")
        # and persistence writes it into agent_results.method_detail (no migration).
        detail = _method_detail(result)
        self.assertEqual(detail["cause"], "catalyst")
        self.assertEqual(detail["cause_rationale"], "검색 선행")


class RuleOnlyRoundTripTest(unittest.TestCase):
    def test_non_cause_source_result_has_no_cause_in_detail(self):
        from app.schemas.source_result import SourceResult

        result = SourceResult(
            source="HIRING", stock_code="005930", direction="positive", score=0.4,
            summary="x", data_status="ok",
        )
        self.assertIsNone(result.cause)
        self.assertNotIn("cause", _method_detail(result))


if __name__ == "__main__":
    unittest.main()
