"""EWMA (RiskMetrics) volatility forecast — CPU baseline.

Recursive variance  v_t = lambda*v_{t-1} + (1-lambda)*r_{t-1}^2  with the
RiskMetrics daily lambda=0.94. The H-day forecast is the flat scaling
sqrt(H * v_last). The simplest sensible baseline every model must beat.

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim).
"""

from __future__ import annotations

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var

NAME = "ewma"


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    r = contract.returns(asof_idx).to_numpy()
    lam = cfg.get("lambda", 0.94)
    if len(r) < 25:
        return h_day_vol_from_daily_var(float(np.var(r)), horizon)
    var = float(np.var(r[:22]))  # seed with first-month sample variance
    for x in r[22:]:
        var = lam * var + (1.0 - lam) * x * x
    return h_day_vol_from_daily_var(var, horizon)


if __name__ == "__main__":
    run_model(predict, NAME)
