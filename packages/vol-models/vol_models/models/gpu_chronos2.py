"""Chronos-2 volatility forecast — GPU. Needs torch + chronos-forecasting.

Feeds the daily VARIANCE series (rv_d) as a univariate context (kept univariate
on purpose, so the comparison vs the price-only models stays apples-to-apples),
forecasts H steps, takes the median path and aggregates to an H-day vol.

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim). ``HAVE_LIB``
lets the model registry skip this model when torch / chronos-forecasting are not
importable (e.g. CPU-only hosts) — see the availability gate in app/ml.

    pip install chronos-forecasting torch    # on the rented GPU box
"""

from __future__ import annotations

import os
import sys

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var, h_day_vol_from_path

NAME = "chronos2"
_PIPE = None

# HF repo 기본값(cfg["model_repo"]로 오버라이드). repo/버전이 바뀌면 ModelSpec.cfg에서 지정.
DEFAULT_MODEL_REPO = "amazon/chronos-2"

try:
    import torch  # noqa: F401
    from chronos import Chronos2Pipeline  # type: ignore

    HAVE_LIB = True
except Exception:  # noqa: BLE001
    HAVE_LIB = False


def _pipeline(model_repo: str):
    global _PIPE
    if _PIPE is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        # TODO(confirm): match the installed chronos-forecasting API/version.
        _PIPE = Chronos2Pipeline.from_pretrained(model_repo, device_map=device)
    return _PIPE


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    import torch

    ctx_len = cfg.get("context_len", 512)
    rv = contract.daily_var(asof_idx).to_numpy()[-ctx_len:]
    # Chronos-2 wants a 3-D tensor: (n_series, n_variates, history_length).
    # One univariate series -> (1, 1, L).
    context = torch.tensor(rv, dtype=torch.float32).reshape(1, 1, -1)
    pipe = _pipeline(cfg.get("model_repo", DEFAULT_MODEL_REPO))
    # Chronos-2 API: ``inputs`` is positional and the call returns (quantiles,
    # mean) as LISTS — one tensor per input series, each shaped
    # [prediction_length, n_quantiles]. We feed one univariate series, request
    # the median (q=0.5), and take series 0.
    quantiles, _mean = pipe.predict_quantiles(
        context, prediction_length=horizon, quantile_levels=[0.5]
    )
    q0 = np.asarray(quantiles[0].detach().cpu()).reshape(horizon, -1)
    median_path = q0[:, 0]
    return h_day_vol_from_path(median_path)


def predict_fallback(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    rv = contract.daily_var(asof_idx).to_numpy()
    return h_day_vol_from_daily_var(float(np.mean(rv[-22:])), horizon)


if __name__ == "__main__":
    if HAVE_LIB:
        run_model(predict, NAME)
    elif os.environ.get("VOL_BENCH_ALLOW_FALLBACK") == "1":
        print("[chronos2] library missing -> FALLBACK proxy (not real Chronos output)")
        run_model(predict_fallback, NAME + "_fallback")
    else:
        sys.exit(
            "chronos-forecasting/torch not installed. Install on the GPU box, or set "
            "VOL_BENCH_ALLOW_FALLBACK=1 to smoke-test the pipeline."
        )
