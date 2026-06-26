"""Bake-off entrypoint: load data -> run every model -> print the comparison table.

Run the synthetic smoke test:

    python -m app.ml.research.bakeoff                # synthetic data, text table
    python -m app.ml.research.bakeoff --csv out.csv  # also dump full metrics as CSV
    python -m app.ml.research.bakeoff --folds 6 --seed 1

When real labels exist, swap ``make_synthetic`` for a loader that pulls features
(via ``features``) and labels (via ``labels`` / ``backtest_results``) — the
``run_bakeoff`` signature is already data-source agnostic.
"""

from __future__ import annotations

import argparse

import numpy as np

from .evaluation import ModelReport, evaluate_model, walk_forward_folds
from .models import build_classifier_registry
from .report import render_csv, render_table
from .synthetic import make_synthetic


def run_bakeoff(
    X: np.ndarray,
    y: np.ndarray,
    excess_returns: np.ndarray,
    dates: np.ndarray,
    *,
    n_folds: int = 5,
    seed: int = 42,
) -> list[ModelReport]:
    """Evaluate the full model registry on one dataset with walk-forward CV."""
    folds = walk_forward_folds(dates, n_folds=n_folds)
    registry = build_classifier_registry(seed=seed)
    reports: list[ModelReport] = []
    for name, model in registry.items():
        reports.append(
            evaluate_model(name, model, X, y, excess_returns, folds)
        )
    return reports


def _load_synthetic(args):
    data = make_synthetic(
        n_stocks=args.stocks,
        n_dates=args.dates,
        noise_scale=args.noise,
        seed=args.seed,
    )
    print(
        f"[synthetic] samples={len(data.y)}  features={data.X.shape[1]}  "
        f"dates={len(np.unique(data.dates))}  up-rate={data.y.mean():.2f}\n"
    )
    return data.X, data.y, data.excess_returns, data.dates


def _load_datalab_demo(args):
    """Run the REAL DataLab pipeline (indicators -> features -> labels) on demo rows."""
    from .datalab_dataset import build_dataset
    from .datalab_demo import generate_demo

    rows, prices, signal_dates, benchmark = generate_demo(
        n_stocks=args.stocks_real, weeks=args.weeks, seed=args.seed
    )
    ds = build_dataset(
        datalab_rows_by_stock=rows,
        prices_by_stock=prices,
        signal_dates_by_stock=signal_dates,
        benchmark=benchmark,
        lookback_days=args.lookback,
        horizon_sessions=args.horizon,
        neutral_band_pct=args.band,
    )
    print(
        f"[datalab-demo] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids))}  dates={len(np.unique(ds.dates))}  "
        f"up-rate={ds.y.mean():.2f}\n  dropped={dict(ds.dropped)}\n"
        f"  features={ds.feature_names}\n"
    )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_datalab_db(args):
    """Load a real dataset from the DB (needs DATABASE_URL + loaded 2021-2023 data)."""
    import asyncio
    import os
    from datetime import date

    from .datalab_db import load_from_env

    try:  # let a repo-root .env supply DATABASE_URL without manual export
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source datalab-db")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    ds = asyncio.run(
        load_from_env(
            database_url=database_url,
            tickers=tickers,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            benchmark_ticker=args.benchmark,
            prices_csv=args.prices_csv,
            lookback_days=args.lookback,
            horizon_sessions=args.horizon,
            neutral_band_pct=args.band,
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[datalab-db] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"up-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — is 2021-2023 DataLab + OHLCV loaded for these tickers? "
            f"dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alternative-data ML model bake-off")
    parser.add_argument(
        "--source",
        choices=["synthetic", "datalab-demo", "datalab-db"],
        default="synthetic",
        help="synthetic matrix, the real DataLab pipeline on demo rows, or live DB",
    )
    parser.add_argument("--folds", type=int, default=5, help="walk-forward folds")
    parser.add_argument("--seed", type=int, default=42, help="model/data seed")
    # synthetic knobs
    parser.add_argument("--stocks", type=int, default=60, help="synthetic stock count")
    parser.add_argument("--dates", type=int, default=40, help="synthetic date count")
    parser.add_argument(
        "--noise", type=float, default=1.0, help="synthetic noise scale (higher=harder)"
    )
    # datalab-demo knobs
    parser.add_argument("--stocks-real", type=int, default=3, help="datalab-demo stocks")
    parser.add_argument("--weeks", type=int, default=104, help="datalab-demo span in weeks")
    parser.add_argument("--lookback", type=int, default=30, help="feature lookback days")
    parser.add_argument("--horizon", type=int, default=5, help="label horizon (sessions)")
    parser.add_argument("--band", type=float, default=0.3, help="neutral band (pct)")
    # datalab-db knobs (real data)
    parser.add_argument("--tickers", type=str, default="005930,000660,035420",
                        help="comma-separated tickers for --source datalab-db")
    parser.add_argument("--start", type=str, default="2021-01-01", help="window start (ISO)")
    parser.add_argument("--end", type=str, default="2023-12-31", help="window end (ISO)")
    parser.add_argument("--benchmark", type=str, default=None,
                        help="benchmark ticker for excess return (e.g. KOSPI 'KS11')")
    parser.add_argument("--prices-csv", type=str, default=None,
                        help="local ticker,date,close CSV for prices (skips ohlcv_data)")
    parser.add_argument("--csv", type=str, default=None, help="write full metrics CSV here")
    args = parser.parse_args(argv)

    if args.source == "datalab-db":
        X, y, excess, dates = _load_datalab_db(args)
    elif args.source == "datalab-demo":
        X, y, excess, dates = _load_datalab_demo(args)
    else:
        X, y, excess, dates = _load_synthetic(args)

    reports = run_bakeoff(X, y, excess, dates, n_folds=args.folds, seed=args.seed)
    print(render_table(reports))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            fh.write(render_csv(reports))
        print(f"\n[csv] wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
