"""LightGBM volatility forecast — CPU (trees). Needs `lightgbm`.

This is the ONLY model that can later absorb the alt-data features (Naver
keywords / job postings / DART / broker reports) — just add columns to the
dataset and extend ``_features``. For the price-only benchmark it trains on
RV/return lag features and predicts the H-day realized VARIANCE directly,
then sqrt to a vol. Point-in-time: it only trains on rows whose H-day target
is already realized as of ``asof``.

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim). ``lightgbm`` is
an optional extra (``signal-alpha-vol-models[lgbm]``); ``HAVE_LGBM`` lets the model
registry skip this model when the backend is not installed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model

NAME = "lightgbm"

try:
    from lightgbm import LGBMRegressor

    HAVE_LGBM = True
except Exception:  # noqa: BLE001
    HAVE_LGBM = False

_RV_LAGS = [1, 2, 3, 5, 10, 22]


def _features(hist: pd.DataFrame) -> pd.DataFrame:
    """Build the lag/rolling feature matrix (extend here for alt-data)."""
    rv, ret = hist["rv_d"], hist["ret"]
    feat = pd.DataFrame(index=hist.index)
    for k in _RV_LAGS:
        feat[f"rv_lag{k}"] = rv.shift(k)
    feat["rv_mean5"] = rv.shift(1).rolling(5).mean()
    feat["rv_mean22"] = rv.shift(1).rolling(22).mean()
    feat["ret_lag1"] = ret.shift(1)
    feat["abs_ret_lag1"] = ret.shift(1).abs()
    feat["ret_std5"] = ret.shift(1).rolling(5).std()
    feat["ret_std22"] = ret.shift(1).rolling(22).std()
    # --- TODO(alt-data): when 5-feature dataset arrives, merge here ---
    # feat["naver_kw_z"] = hist["naver_kw_z"].shift(1)
    # feat["dart_event_decay"] = hist["dart_event_decay"].shift(1)
    return feat


def _target_h_var(hist: pd.DataFrame, horizon: int) -> pd.Series:
    """Future H-day realized variance per row (NaN where not yet realized)."""
    r2 = hist["ret"] ** 2
    fwd = r2.shift(-1).rolling(horizon).sum().shift(-(horizon - 1))
    return fwd


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    if not HAVE_LGBM:
        raise RuntimeError("`lightgbm` not installed — pip install lightgbm")
    hist = contract.history(asof_idx).reset_index(drop=True)
    feat = _features(hist)
    target = _target_h_var(hist, horizon)

    train = feat.notna().all(axis=1) & target.notna()
    if train.sum() < 60:
        return float(np.sqrt(max(hist["rv_d"].iloc[-1] * horizon, 1e-12)))

    model = LGBMRegressor(
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=cfg.get("seed", 42),
        n_jobs=-1,
        verbose=-1,
    )
    model.fit(feat[train], target[train])
    x_asof = feat.iloc[[-1]]
    pred_var = max(float(model.predict(x_asof)[0]), 1e-12)
    return float(np.sqrt(pred_var))


if __name__ == "__main__":
    run_model(predict, NAME)
