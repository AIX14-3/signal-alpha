"""Walk-forward (rolling-origin) backtest harness — shared by all models.

Design (agreed in the plan):
  * ONE walk-forward pass over the full ~3 years per ticker.
  * Warmup window first (GARCH/LightGBM need history); then a rolling test
    window where a forecast is produced every ``step`` trading days.
  * Each forecast at origin ``asof`` uses only data <= asof (via DataContract);
    the realized target uses only future returns (via rv.future_realized_vol).
  * Every model writes the SAME schema so score.py can compare them directly.

A model module just implements::

    NAME = "ewma"
    def predict(contract, asof_idx, horizon, cfg, rng) -> float: ...

and calls ``run_model(predict, NAME, args)``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .data_contract import DataContract
from .rv import future_realized_vol

RESULT_COLUMNS = [
    "model",
    "ticker",
    "asof_date",
    "horizon",
    "pred_vol",
    "actual_vol",
    "runtime_sec",
    "peak_mem_mb",
    "seed",
]

PredictFn = Callable[[DataContract, int, int, dict, np.random.Generator], float]


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", default="dataset.parquet", help="frozen dataset path")
    parser.add_argument("--out", default="results.csv", help="results CSV (appended)")
    parser.add_argument("--horizon", type=int, default=10, help="forecast horizon H (days)")
    parser.add_argument("--warmup", type=int, default=375, help="warmup trading days (~1.5y)")
    parser.add_argument("--step", type=int, default=5, help="forecast cadence in test window")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tickers", default="", help="comma list; default = all in data")
    parser.add_argument(
        "--jobs", type=int, default=1,
        help="ticker-level parallelism (>1). Predictions are identical to --jobs 1 "
        "(same per-forecast code path); only runtime/peak_mem measurements get noisier "
        "under contention — measure cost with --jobs 1 if you need clean numbers.",
    )


def _load(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, parse_dates=["date"], dtype={"ticker": str})


def _peak_mem_mb() -> float:
    """Best-effort peak RSS in MB (psutil if present, else 0)."""
    try:
        import psutil  # type: ignore

        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:
        return 0.0


def _ticker_rows(predict: PredictFn, name: str, frame: pd.DataFrame, ticker: str,
                 horizon: int, warmup: int, step: int, seed: int, cfg: dict) -> list[dict]:
    """Walk-forward for ONE ticker. Pure function of (frame, seed) -> identical
    output whether called sequentially or in a worker process."""
    contract = DataContract(frame)
    ret = contract.frame["ret"]
    rng = np.random.default_rng(seed)
    last = len(contract) - horizon - 1
    origins = list(range(warmup, last + 1, step))
    rows: list[dict] = []
    for i, asof_idx in enumerate(origins, 1):
        if i % 10 == 0 or i == len(origins):
            print(f"[{name}/{ticker}] {i}/{len(origins)} origins", flush=True)
        actual = future_realized_vol(ret, asof_idx, horizon)
        if actual is None:
            continue
        t0 = time.perf_counter()
        try:
            pred = float(predict(contract, asof_idx, horizon, cfg, rng))
        except Exception as exc:  # noqa: BLE001 — keep the run going, log the gap
            print(f"[{name}/{ticker}] asof_idx={asof_idx} predict failed: {exc}")
            continue
        rows.append({
            "model": name, "ticker": ticker, "asof_date": contract.asof_date(asof_idx),
            "horizon": horizon, "pred_vol": pred, "actual_vol": actual,
            "runtime_sec": round(time.perf_counter() - t0, 4),
            "peak_mem_mb": round(_peak_mem_mb(), 1), "seed": seed,
        })
    return rows


_MODCACHE: dict = {}


def _load_predict(fn_file: str, fn_name: str) -> PredictFn:
    """Load a model module by file path (NOT '__main__') so its predict fn is
    importable in a worker without re-triggering its run_model() entrypoint."""
    mod = _MODCACHE.get(fn_file)
    if mod is None:
        spec = importlib.util.spec_from_file_location(f"_vbmodel_{abs(hash(fn_file))}", fn_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # __name__ != '__main__' -> entrypoint skipped
        _MODCACHE[fn_file] = mod
    return getattr(mod, fn_name)


def _pool_worker(payload: tuple) -> list[dict]:
    fn_file, fn_name, name, frame, ticker, horizon, warmup, step, seed, cfg = payload
    predict = _load_predict(fn_file, fn_name)
    return _ticker_rows(predict, name, frame, ticker, horizon, warmup, step, seed, cfg)


def run_model(predict: PredictFn, name: str, cfg: dict | None = None) -> None:
    """Parse args, run the walk-forward for ``predict``, append results."""
    parser = argparse.ArgumentParser(description=f"vol-benchmark model: {name}")
    add_common_args(parser)
    args = parser.parse_args()
    cfg = dict(cfg or {})

    data = _load(args.data)
    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else sorted(data["ticker"].unique())
    )

    rows: list[dict] = []
    if args.jobs and args.jobs > 1 and len(tickers) > 1:
        # ticker-level parallelism — same per-forecast code path, identical numbers.
        fn_file, fn_name = predict.__code__.co_filename, predict.__name__
        payloads = [
            (fn_file, fn_name, name, data[data["ticker"] == t].copy(), t,
             args.horizon, args.warmup, args.step, args.seed, cfg)
            for t in tickers
        ]
        print(f"[{name}] parallel over {len(tickers)} tickers, jobs={args.jobs}", flush=True)
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            for r in ex.map(_pool_worker, payloads):
                rows.extend(r)
    else:
        for ticker in tickers:
            rows.extend(_ticker_rows(predict, name, data[data["ticker"] == ticker], ticker,
                                     args.horizon, args.warmup, args.step, args.seed, cfg))

    if not rows:
        print(f"[{name}] produced no forecasts — check --warmup/--horizon vs data length")
        return

    out = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    header = not os.path.exists(args.out)
    out.to_csv(args.out, mode="a", header=header, index=False)
    print(f"[{name}] wrote {len(out)} forecasts -> {args.out}")
