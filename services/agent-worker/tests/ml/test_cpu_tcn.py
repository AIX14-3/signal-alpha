"""cpu_tcn (lightweight DL line): registry wiring + a CPU predict smoke test.

The predict test is skipped where torch is not installed — the availability gate
does the same in production, so the model simply does not run there.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.ml import model_registry as reg
from app.ml.contract_adapter import build_contract

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


def test_tcn_is_a_cpu_candidate_in_default_gate() -> None:
    spec = {s.name: s for s in reg.all_specs()}["tcn"]
    assert spec.device == "cpu"
    assert "tcn" in reg.gate_passed_names()


def test_tcn_availability_follows_torch() -> None:
    spec = {s.name: s for s in reg.all_specs()}["tcn"]
    torch = pytest.importorskip("torch") if spec.is_available() else None
    if torch is None:
        # torch absent → availability gate excludes tcn (no crash, just skipped)
        assert reg.resolve_models(enabled=["tcn"]) == []


def test_tcn_predicts_finite_positive_vol_on_cpu() -> None:
    pytest.importorskip("torch")
    from vol_models.models import cpu_tcn

    contract = _contract(220)
    asof_idx = len(contract) - 1
    # small/fast config keeps the CPU test quick while exercising the full path
    cfg = {"seed": 42, "window": 32, "epochs": 20, "hidden": 8, "min_train": 40}
    vol = cpu_tcn.predict(contract, asof_idx, horizon=10, cfg=cfg, rng=None)

    assert isinstance(vol, float)
    assert math.isfinite(vol) and vol > 0.0


def test_tcn_falls_back_when_history_too_short() -> None:
    pytest.importorskip("torch")
    from vol_models.models import cpu_tcn

    contract = _contract(40)  # below window+horizon → flat RV fallback, no training
    asof_idx = len(contract) - 1
    vol = cpu_tcn.predict(contract, asof_idx, horizon=10, cfg={"window": 48}, rng=None)
    assert math.isfinite(vol) and vol > 0.0
