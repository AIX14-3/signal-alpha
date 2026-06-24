"""GARCH(1,1) volatility forecast — CPU. Needs the `arch` package.

Refit GARCH(1,1) on close-to-close returns <= asof, forecast the variance path
for the next H days, sum and sqrt -> H-day realized-vol forecast. Returns are
scaled x100 for the optimiser's numerical stability (arch convention).

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim). ``arch`` is an
optional extra (``signal-alpha-vol-models[garch]``); ``HAVE_ARCH`` lets the model
registry skip this model when the backend is not installed.
"""

from __future__ import annotations

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var, h_day_vol_from_path

NAME = "garch"

try:
    from arch import arch_model

    HAVE_ARCH = True
except Exception:  # noqa: BLE001
    HAVE_ARCH = False


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    if not HAVE_ARCH:
        raise RuntimeError("`arch` not installed — pip install arch")
    r = contract.returns(asof_idx).to_numpy() * 100.0
    if len(r) < 100:
        return h_day_vol_from_daily_var(float(np.var(r / 100.0)), horizon)
    am = arch_model(r, mean="Zero", vol="GARCH", p=1, q=1, dist="normal")
    res = am.fit(disp="off")
    fc = res.forecast(horizon=horizon, reindex=False)
    var_path = np.asarray(fc.variance.values[-1]) / (100.0**2)  # back to return scale
    return h_day_vol_from_path(var_path)


if __name__ == "__main__":
    run_model(predict, NAME)
