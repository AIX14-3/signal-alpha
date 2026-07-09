"""소스별 블렌드 가중치(equal/ic/confidence/ic_confidence) 단위 테스트.

기본(equal)은 현행 등가중 1/N 과 정확히 동일함을 회귀로 고정하고, ic/confidence 모드에서
가중 평균·정규화 지분(share)·기여도(contribution)가 맞는지 검증한다.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.analyzers.config import AggregatorConfig
from app.orchestrator.aggregation.tasks import (
    NormalizedSourceResult,
    _aggregate,
    _blend_basis,
    _confidence_weight,
    _source_weight,
)


def _nsr(source, score, *, data_status="ok", risk_flags=None, direction="positive"):
    return NormalizedSourceResult(
        source=source,
        analysis_result_id=1,
        agent_result_id=1,
        direction=direction,
        score=score,
        score_100=round(score * 50 + 50, 2),
        data_status=data_status,
        needs_review=False,
        risk_flags=list(risk_flags or []),
        summary="s",
        source_signal_event_ids=[],
        valuation=None,
    )


class SourceWeightTest(unittest.TestCase):
    def test_equal_mode_all_weights_one(self):
        cfg = AggregatorConfig(weights={}, weight_mode="equal")
        r = _nsr("PATENT", 0.4)
        self.assertEqual(_source_weight(r, cfg), 1.0)

    def test_ic_mode_uses_weights_table_with_fallback(self):
        cfg = AggregatorConfig(weights={"PATENT": 3.0}, weight_mode="ic")
        self.assertEqual(_source_weight(_nsr("PATENT", 0.4), cfg), 3.0)
        # 미등록 소스는 1.0 폴백.
        self.assertEqual(_source_weight(_nsr("HIRING", 0.2), cfg), 1.0)

    def test_confidence_weight_penalizes_degraded_data(self):
        cfg = AggregatorConfig(weights={}, weight_mode="confidence")
        self.assertEqual(_confidence_weight(_nsr("PATENT", 0.4), cfg), 1.0)  # ok
        # partial → *partial_penalty(0.8)
        self.assertAlmostEqual(
            _confidence_weight(_nsr("PATENT", 0.4, data_status="partial"), cfg),
            cfg.partial_penalty,
        )
        # stale_data → *stale_penalty(0.85)
        self.assertAlmostEqual(
            _confidence_weight(_nsr("PATENT", 0.4, risk_flags=["stale_data"]), cfg),
            cfg.stale_penalty,
        )
        # partial + insufficient_history → 두 페널티 곱
        self.assertAlmostEqual(
            _confidence_weight(
                _nsr("PATENT", 0.4, data_status="partial", risk_flags=["insufficient_history"]),
                cfg,
            ),
            cfg.partial_penalty * cfg.sparse_penalty,
        )

    def test_ic_confidence_is_product(self):
        cfg = AggregatorConfig(weights={"PATENT": 2.0}, weight_mode="ic_confidence")
        r = _nsr("PATENT", 0.4, data_status="partial")  # ic 2.0 × conf 0.8
        self.assertAlmostEqual(_source_weight(r, cfg), 2.0 * cfg.partial_penalty)


class AggregateWeightingTest(unittest.TestCase):
    def test_equal_mode_matches_simple_mean(self):
        cfg = AggregatorConfig(weights={}, weight_mode="equal")
        rows = [_nsr("PATENT", 0.4), _nsr("HIRING", 0.2)]
        out = _aggregate(rows, cfg)
        self.assertAlmostEqual(out["blend_basis"]["blend_score"], 0.3)  # 등가중 평균
        shares = [s["share"] for s in out["blend_basis"]["sources"]]
        self.assertEqual(shares, [0.5, 0.5])
        self.assertAlmostEqual(sum(shares), 1.0)

    def test_ic_mode_weighted_mean_and_basis(self):
        cfg = AggregatorConfig(weights={"PATENT": 3.0, "HIRING": 1.0}, weight_mode="ic")
        rows = [_nsr("PATENT", 0.4), _nsr("HIRING", 0.2)]
        out = _aggregate(rows, cfg)
        # (3*0.4 + 1*0.2) / 4 = 0.35
        self.assertAlmostEqual(out["blend_basis"]["blend_score"], 0.35)
        by_src = {s["source"]: s for s in out["blend_basis"]["sources"]}
        self.assertAlmostEqual(by_src["PATENT"]["share"], 0.75)
        self.assertAlmostEqual(by_src["HIRING"]["share"], 0.25)
        self.assertAlmostEqual(by_src["PATENT"]["contribution"], 0.75 * 0.4)
        self.assertAlmostEqual(by_src["HIRING"]["contribution"], 0.25 * 0.2)

    def test_confidence_mode_downweights_partial_source(self):
        cfg = AggregatorConfig(weights={}, weight_mode="confidence")
        rows = [_nsr("PATENT", 0.4), _nsr("HIRING", 0.2, data_status="partial")]
        out = _aggregate(rows, cfg)
        # (1*0.4 + 0.8*0.2) / 1.8
        expected = (0.4 + cfg.partial_penalty * 0.2) / (1.0 + cfg.partial_penalty)
        self.assertAlmostEqual(out["blend_basis"]["blend_score"], round(expected, 3))
        by_src = {s["source"]: s for s in out["blend_basis"]["sources"]}
        self.assertGreater(by_src["PATENT"]["share"], by_src["HIRING"]["share"])

    def test_all_zero_weights_yield_zero_and_empty_basis(self):
        # ic 모드에서 전 소스 가중 0 → 0으로 폴백, 소스 목록 비움(0 나눗셈 방지).
        cfg = AggregatorConfig(weights={"PATENT": 0.0, "HIRING": 0.0}, weight_mode="ic")
        rows = [_nsr("PATENT", 0.4), _nsr("HIRING", 0.2)]
        basis = _blend_basis([(r, _source_weight(r, cfg)) for r in rows], 0.0)
        self.assertEqual(basis["sources"], [])
        self.assertEqual(basis["blend_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
