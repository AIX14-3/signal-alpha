"""Regime tagging layer — non-verdict + default-OFF invariants (Track A stub).

No real network calls: a fake client stands in for GeminiJsonClient.
"""

import unittest
from datetime import date
from types import SimpleNamespace

from app.agents.regime import (
    REGIME_LABELS,
    RegimeClassifier,
    RegimeTag,
    build_regime_tagger,
    classify_regime,
    regime_features,
)
from app.ml.source_features import assemble_features


class FakeClient:
    """Mimics GeminiJsonClient: ``.model`` + async ``generate_json``. No network."""

    def __init__(self, *, payload=None, raise_exc=None, model="fake-regime-model"):
        self.model = model
        self._payload = payload
        self._raise = raise_exc
        self.calls = 0

    async def generate_json(self, prompt):
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._payload


_CONTEXT = {"sector_return_dispersion": 0.031, "sector_return_mean": 0.004}


class RegimeFactoryTest(unittest.TestCase):
    def test_factory_none_when_flag_off(self):
        settings = SimpleNamespace(regime_use_llm=False, gemini_api_key="k", regime_llm_model="m")
        self.assertIsNone(build_regime_tagger(settings))

    def test_factory_none_when_no_key(self):
        settings = SimpleNamespace(regime_use_llm=True, gemini_api_key="", regime_llm_model="m")
        self.assertIsNone(build_regime_tagger(settings))

    def test_factory_builds_when_enabled_with_key(self):
        settings = SimpleNamespace(regime_use_llm=True, gemini_api_key="k", regime_llm_model="")
        self.assertIsInstance(build_regime_tagger(settings), RegimeClassifier)


class RegimeClassifyTest(unittest.IsolatedAsyncioTestCase):
    async def test_valid_payload_maps_to_enum_tag(self):
        client = FakeClient(payload={
            "regime": "ai_capex_boom",
            "rationale": "반도체 섹터 쏠림이 두드러진다.",
            "confidence": 0.8,
        })
        tag = await classify_regime(_CONTEXT, client=client)
        self.assertEqual(tag.label, "ai_capex_boom")
        self.assertIn(tag.label, REGIME_LABELS)
        self.assertEqual(tag.model, "fake-regime-model")
        self.assertIsNone(tag.error)

    async def test_malformed_json_degrades_to_label_none(self):
        # Non-dict payload → deterministic degrade (never a fabricated tag).
        client = FakeClient(payload=["not", "a", "dict"])
        tag = await classify_regime(_CONTEXT, client=client)
        self.assertIsNone(tag.label)
        self.assertIsNotNone(tag.error)

    async def test_out_of_enum_label_degrades(self):
        client = FakeClient(payload={"regime": "moon_phase", "rationale": "x"})
        tag = await classify_regime(_CONTEXT, client=client)
        self.assertIsNone(tag.label)

    async def test_transport_exception_degrades(self):
        client = FakeClient(raise_exc=RuntimeError("timeout"))
        tag = await classify_regime(_CONTEXT, client=client)
        self.assertIsNone(tag.label)
        self.assertIn("RuntimeError", tag.error or "")

    async def test_investment_advice_rationale_is_rejected(self):
        client = FakeClient(payload={
            "regime": "risk_on",
            "rationale": "지금 매수하세요 목표주가 10만원.",
            "confidence": 0.9,
        })
        tag = await classify_regime(_CONTEXT, client=client)
        self.assertIsNone(tag.label)  # policy guard → deterministic degrade
        self.assertIn("policy", tag.error or "")

    async def test_tag_never_carries_score_or_direction(self):
        client = FakeClient(payload={"regime": "neutral", "rationale": "뚜렷한 레짐 없음."})
        tag = await classify_regime(_CONTEXT, client=client)
        fields = RegimeTag.__dataclass_fields__
        for banned in ("score", "direction", "verdict", "target_price"):
            self.assertNotIn(banned, fields)
        # confidence exists but is display-only provenance, not a signed number.
        self.assertGreaterEqual(tag.confidence, 0.0)
        self.assertLessEqual(tag.confidence, 1.0)

    async def test_classifier_wrapper_delegates(self):
        client = FakeClient(payload={"regime": "credit_stress", "rationale": "스프레드 확대."})
        tag = await RegimeClassifier(client).classify(_CONTEXT)
        self.assertEqual(tag.label, "credit_stress")


class RegimeFeaturesTest(unittest.TestCase):
    def _rows(self):
        return [
            {"date": "2026-06-30", "sector": "semis", "return": 0.05},
            {"date": "2026-06-30", "sector": "banks", "return": -0.01},
            {"date": "2026-06-30", "sector": "utils", "return": 0.00},
            # future row — must be dropped by the PIT gate:
            {"date": "2026-07-10", "sector": "semis", "return": 0.99},
        ]

    def test_is_point_in_time_drops_future_rows(self):
        asof = date(2026, 6, 30)
        feat = regime_features(asof, sector_return_rows=self._rows())
        # 3 sectors on 06-30; the 07-10 future row is excluded.
        self.assertEqual(feat["sector_count"], 3.0)
        self.assertAlmostEqual(feat["sector_return_spread"], 0.06)  # 0.05 - (-0.01)
        self.assertIsNotNone(feat["sector_return_dispersion"])

    def test_deterministic_same_input_same_output(self):
        asof = date(2026, 6, 30)
        a = regime_features(asof, sector_return_rows=self._rows())
        b = regime_features(asof, sector_return_rows=self._rows())
        self.assertEqual(a, b)

    def test_empty_returns_all_none(self):
        feat = regime_features(date(2026, 6, 30), sector_return_rows=[])
        self.assertTrue(all(v is None for v in feat.values()))

    def test_future_only_rows_yield_none(self):
        rows = [{"date": "2026-08-01", "sector": "semis", "return": 0.1}]
        feat = regime_features(date(2026, 6, 30), sector_return_rows=rows)
        self.assertTrue(all(v is None for v in feat.values()))


class AssembleFeaturesHookTest(unittest.TestCase):
    def test_backward_compatible_without_regime_rows(self):
        # Existing callers pass no regime_rows → no "regime" block (byte-identical).
        out = assemble_features(date(2026, 6, 30))
        self.assertNotIn("regime", out)

    def test_regime_block_added_when_rows_provided(self):
        rows = [
            {"date": "2026-06-30", "sector": "semis", "return": 0.05},
            {"date": "2026-06-30", "sector": "banks", "return": -0.01},
        ]
        out = assemble_features(date(2026, 6, 30), regime_rows=rows)
        self.assertIn("regime", out)
        self.assertEqual(out["regime"]["sector_count"], 2.0)


if __name__ == "__main__":
    unittest.main()
