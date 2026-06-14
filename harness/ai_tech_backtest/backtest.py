"""Walk-forward, out-of-sample backtest engine.

For each symbol/horizon we slide a (train, test) window across history. The
rule model needs no training; the ML model is fit ONLY on each fold's train
window. Predictions are collected from test windows only, so every reported
number is out-of-sample. Results are then split by regime (full history vs the
post-ChatGPT AI era) and a label-shuffle placebo checks for leakage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import AI_ERA_START, COST_PER_TRADE, HORIZONS, STEP, TEST_WINDOW, TRAIN_WINDOW
from indicators import build_features
from ingest import load_ohlcv
from labeling import add_labels
import metrics
from signals import MLModel, rule_predict
from universe import UNIVERSE

MODELS = ("rule", "ml")


def _folds(n: int):
    i = TRAIN_WINDOW
    while i + TEST_WINDOW <= n:
        yield (i - TRAIN_WINDOW, i, min(i + TEST_WINDOW, n))
        i += STEP


def _collect_symbol(df: pd.DataFrame) -> dict:
    """Return OOS arrays per (model, horizon): preds, labels, rets, dates."""
    feat = add_labels(build_features(df))
    out: dict = {m: {h: {"pred": [], "label": [], "ret": [], "date": []}
                     for h in HORIZONS} for m in MODELS}

    for h in HORIZONS:
        label_col, ret_col = f"label_{h}", f"ret_{h}"
        for a, b, c in _folds(len(feat)):
            train = feat.iloc[a:b]
            test = feat.iloc[b:c]
            if test.empty:
                continue

            rule_p = rule_predict(test)
            ml = MLModel().fit(train, label_col)
            ml_p = ml.predict(test) if ml.fitted else rule_p  # fallback if unfit

            for m, preds in (("rule", rule_p), ("ml", ml_p)):
                bucket = out[m][h]
                bucket["pred"].append(preds)
                bucket["label"].append(test[label_col].to_numpy())
                bucket["ret"].append(test[ret_col].to_numpy())
                bucket["date"].append(test["date"].to_numpy())

    # Concatenate folds.
    for m in MODELS:
        for h in HORIZONS:
            b = out[m][h]
            for k in b:
                b[k] = np.concatenate(b[k]) if b[k] else np.array([])
    return out


def _regime_metrics(b: dict) -> dict:
    dates = pd.to_datetime(b["date"])
    ai_mask = dates >= pd.Timestamp(AI_ERA_START)
    full = metrics.directional(b["pred"], b["label"], b["ret"])
    ai = metrics.directional(b["pred"][ai_mask], b["label"][ai_mask], b["ret"][ai_mask])
    pre = metrics.directional(b["pred"][~ai_mask], b["label"][~ai_mask], b["ret"][~ai_mask])
    eq = metrics.equity_curve(b["pred"], b["ret"], COST_PER_TRADE)
    return {"all": full, "ai_era": ai, "pre_ai": pre, "equity": eq}


def run_backtest() -> dict:
    per_symbol: dict = {}
    pooled: dict = {m: {h: {"pred": [], "label": [], "ret": [], "date": []}
                        for h in HORIZONS} for m in MODELS}

    for inst in UNIVERSE:
        try:
            df = load_ohlcv(inst.symbol)
        except FileNotFoundError:
            print(f"  [skip] {inst.symbol} (no data)")
            continue
        if len(df) < TRAIN_WINDOW + TEST_WINDOW:
            print(f"  [skip] {inst.symbol} (history too short: {len(df)})")
            continue

        sym = _collect_symbol(df)
        per_symbol[inst.symbol] = {
            h: {m: metrics.directional(sym[m][h]["pred"], sym[m][h]["label"], sym[m][h]["ret"])
                for m in MODELS}
            for h in HORIZONS
        }
        for m in MODELS:
            for h in HORIZONS:
                for k in pooled[m][h]:
                    pooled[m][h][k].append(sym[m][h][k])
        print(f"  [done] {inst.symbol}")

    # Pool across symbols.
    for m in MODELS:
        for h in HORIZONS:
            for k in pooled[m][h]:
                arrs = [a for a in pooled[m][h][k] if len(a)]
                pooled[m][h][k] = np.concatenate(arrs) if arrs else np.array([])

    per_horizon = {
        h: {m: _regime_metrics(pooled[m][h]) for m in MODELS} for h in HORIZONS
    }

    # Leakage placebo: shuffle labels for the rule model; accuracy should ~0.5.
    rng = np.random.default_rng(7)
    placebo = {}
    for h in HORIZONS:
        b = pooled["rule"][h]
        if len(b["label"]):
            shuffled = b["label"].copy()
            rng.shuffle(shuffled)
            placebo[h] = metrics.directional(b["pred"], shuffled, b["ret"]).get("accuracy")
        else:
            placebo[h] = None

    return {"per_horizon": per_horizon, "per_symbol": per_symbol,
            "placebo": placebo, "pooled": pooled}
