"""Kronos volatility forecast — GPU. Needs the cloned Kronos repo + torch.

Kronos is candle-native and GENERATIVE: it consumes OHLCV and samples future
candle paths. For an accurate point estimate we sample N paths, derive the
H-day realized vol from each predicted close-to-close path, and average — a
single sample is too noisy to compare fairly.

Setup on the GPU box:
    git clone https://github.com/shiyu-coder/Kronos   # NeoQuasar/Kronos-base
    pip install torch einops huggingface_hub
    # ensure the repo's `model` package (Kronos, KronosTokenizer, KronosPredictor)
    # is importable, e.g. PYTHONPATH=/path/to/Kronos

Vendored from vol-benchmark; only the import paths were changed to the
``vol_models.common.*`` package layout (logic preserved verbatim). ``HAVE_LIB``
lets the model registry skip this model when torch / the Kronos repo are not
importable (e.g. CPU-only hosts) — see the availability gate in app/ml.
"""

from __future__ import annotations

import os
import sys

import numpy as np

from vol_models.common.data_contract import DataContract
from vol_models.common.harness import run_model
from vol_models.common.rv import h_day_vol_from_daily_var

NAME = "kronos"
_PREDICTOR = None

# HF repo 기본값(cfg["tokenizer_repo"]/["model_repo"]로 오버라이드). repo/버전이 바뀌면
# 코드 수정 없이 ModelSpec.cfg에서 지정한다.
DEFAULT_TOKENIZER_REPO = "NeoQuasar/Kronos-Tokenizer-base"
DEFAULT_MODEL_REPO = "NeoQuasar/Kronos-base"

try:
    import torch  # noqa: F401
    from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore

    HAVE_LIB = True
except Exception:  # noqa: BLE001
    HAVE_LIB = False


def _predictor(max_context: int, tokenizer_repo: str, model_repo: str):
    global _PREDICTOR
    if _PREDICTOR is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        # TODO(confirm): repo ids / signature against the cloned Kronos repo.
        tok = KronosTokenizer.from_pretrained(tokenizer_repo)
        model = Kronos.from_pretrained(model_repo)
        _PREDICTOR = KronosPredictor(model, tok, device=device, max_context=max_context)
    return _PREDICTOR


def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    import pandas as pd

    ctx_len = cfg.get("context_len", 400)
    n_samples = cfg.get("n_samples", 100)
    hist = contract.history(asof_idx).iloc[-ctx_len:]
    last_close = float(hist["close"].iloc[-1])

    df = hist[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    x_ts = pd.to_datetime(contract.history(asof_idx)["date"].iloc[-ctx_len:]).reset_index(drop=True)
    y_ts = pd.bdate_range(start=x_ts.iloc[-1], periods=horizon + 1)[1:].to_series().reset_index(
        drop=True
    )

    predictor = _predictor(
        ctx_len,
        cfg.get("tokenizer_repo", DEFAULT_TOKENIZER_REPO),
        cfg.get("model_repo", DEFAULT_MODEL_REPO),
    )
    vols = []
    for _ in range(n_samples):
        pred_df = predictor.predict(
            df=df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=horizon,
            T=cfg.get("T", 1.0), top_p=cfg.get("top_p", 0.9), sample_count=1,
        )
        closes = np.concatenate([[last_close], np.asarray(pred_df["close"], dtype=float)])
        # A generative path can occasionally sample a non-positive / non-finite
        # close; log() would make that whole origin NaN. Skip such paths and
        # average over the valid ones instead of poisoning the estimate.
        if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
            continue
        rets = np.diff(np.log(closes))
        vols.append(np.sqrt(np.sum(rets**2)))
    if not vols:
        raise ValueError("all Kronos sample paths were non-finite/non-positive")
    return float(np.mean(vols))


def predict_fallback(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float:
    rv = contract.daily_var(asof_idx).to_numpy()
    return h_day_vol_from_daily_var(float(np.mean(rv[-22:])), horizon)


if __name__ == "__main__":
    if HAVE_LIB:
        run_model(predict, NAME, cfg={"n_samples": 100, "context_len": 400})
    elif os.environ.get("VOL_BENCH_ALLOW_FALLBACK") == "1":
        print("[kronos] library missing -> FALLBACK proxy (not real Kronos output)")
        run_model(predict_fallback, NAME + "_fallback")
    else:
        sys.exit(
            "Kronos repo/torch not importable. Clone the repo + set PYTHONPATH on the GPU "
            "box, or set VOL_BENCH_ALLOW_FALLBACK=1 to smoke-test the pipeline."
        )
