"""run_inference: runs gate-passed models on a DataContract, isolates failures."""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.ml.contract_adapter import build_contract
from app.ml.inference import InferenceResult, run_inference
from app.ml.model_registry import resolve_models

_START = date(2026, 1, 1)


def _contract(n: int):
    closes = [100 + 5 * math.sin(i / 4) + i * 0.05 for i in range(n)]
    rows = [
        {
            "trade_date": (_START + timedelta(days=i)).isoformat(),
            "open": c * 0.99,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 1000 + i,
        }
        for i, c in enumerate(closes)
    ]
    return build_contract(rows, ticker="005930")


def test_run_inference_produces_finite_predictions_for_cpu_models() -> None:
    contract = _contract(80)
    specs = resolve_models(enabled=["ewma", "har_rv"])
    results = run_inference(contract, horizon=10, specs=specs, seed=42)

    assert {r.model_name for r in results} == {"ewma", "har_rv"}
    for r in results:
        assert r.error is None
        assert r.pred_value is not None and math.isfinite(r.pred_value)
        assert r.pred_value > 0
        assert r.horizon == 10


def test_run_inference_isolates_a_failing_model() -> None:
    class _BoomSpec:
        name = "boom"
        device = "cpu"
        cfg: dict = {}

        def load(self):
            def _predict(contract, asof_idx, horizon, cfg, rng):
                raise RuntimeError("backend exploded")

            return _predict

    contract = _contract(40)
    results = run_inference(contract, horizon=10, specs=[_BoomSpec()], seed=1)

    assert len(results) == 1
    assert isinstance(results[0], InferenceResult)
    assert results[0].pred_value is None
    assert "backend exploded" in results[0].error


def test_run_inference_flags_non_finite_prediction_as_error() -> None:
    class _NanSpec:
        name = "nan"
        device = "cpu"
        cfg: dict = {}

        def load(self):
            return lambda contract, asof_idx, horizon, cfg, rng: float("nan")

    contract = _contract(40)
    results = run_inference(contract, horizon=10, specs=[_NanSpec()], seed=1)

    assert results[0].pred_value is None
    assert "non-finite" in results[0].error
