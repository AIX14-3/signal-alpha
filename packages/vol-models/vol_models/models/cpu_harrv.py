"""HAR-RV volatility forecast — CPU. The standard realized-vol benchmark.

Corsi (2009): regress daily variance on its daily / weekly(5) / monthly(22)
averages. Refit by OLS on all history <= asof each origin, predict next-day
variance, scale to H days. Hard to beat — treat as the model to dethrone.

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim).
"""

from __future__ import annotations

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var

NAME = "har_rv"


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    rv = contract.daily_var(asof_idx).to_numpy()
    m = len(rv)
    if m < 30:
        return h_day_vol_from_daily_var(float(rv[-1]), horizon)

    # design rows for t in [22, m): predictors use rv[<t] only
    rows, y = [], []
    for t in range(22, m):
        rows.append([1.0, rv[t - 1], rv[t - 5 : t].mean(), rv[t - 22 : t].mean()])
        y.append(rv[t])
    x = np.asarray(rows)
    beta, *_ = np.linalg.lstsq(x, np.asarray(y), rcond=None)

    xstar = np.array([1.0, rv[m - 1], rv[m - 5 : m].mean(), rv[m - 22 : m].mean()])
    pred_daily_var = max(float(xstar @ beta), 1e-12)
    return h_day_vol_from_daily_var(pred_daily_var, horizon)


if __name__ == "__main__":
    run_model(predict, NAME)
