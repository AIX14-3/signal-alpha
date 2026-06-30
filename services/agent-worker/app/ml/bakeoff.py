"""Bake-off entrypoint: load data -> run every model -> print the comparison table.

Run the synthetic smoke test:

    python -m app.ml.bakeoff                # synthetic data, text table
    python -m app.ml.bakeoff --csv out.csv  # also dump full metrics as CSV
    python -m app.ml.bakeoff --folds 6 --seed 1

When real labels exist, swap ``make_synthetic`` for a loader that pulls features
(via ``features``) and labels (via ``labels`` / ``backtest_results``) — the
``run_bakeoff`` signature is already data-source agnostic.
"""

from __future__ import annotations

import argparse

import numpy as np

from .evaluation import ModelReport, evaluate_model, walk_forward_folds
from .models import build_classifier_registry, build_regressor_registry
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
    task: str = "direction",
) -> list[ModelReport]:
    """Evaluate the full model registry on one dataset with walk-forward CV.

    ``task='direction'`` runs the classifier zoo; ``task='magnitude'`` runs the
    regressor zoo against the continuous magnitude target.
    """
    folds = walk_forward_folds(dates, n_folds=n_folds)
    registry = (
        build_regressor_registry(seed=seed)
        if task == "magnitude"
        else build_classifier_registry(seed=seed)
    )
    reports: list[ModelReport] = []
    for name, model in registry.items():
        reports.append(
            evaluate_model(name, model, X, y, excess_returns, folds, task=task)
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
            signal_step=args.signal_step,
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


def _load_period_keyword(args):
    """Patent-derived period-keyword DataLab features (Stage 5), DB-free from CSVs."""
    from datetime import date

    from .period_keyword_dataset import load_period_keyword_dataset, resolve_meta_paths

    if not args.prices_csv:
        raise SystemExit("--prices-csv is required for --source period-keyword")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    meta_paths = resolve_meta_paths(args.keyword_meta)
    ds = load_period_keyword_dataset(
        keyword_csv=args.keyword_csv,
        meta_paths=meta_paths,
        prices_csv=args.prices_csv,
        tickers=tickers,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        benchmark_ticker=args.benchmark,
        feature_mode=args.feature_mode,
        lookback_days=args.lookback,
        horizon_sessions=args.horizon,
        neutral_band_pct=args.band,
        signal_step=args.signal_step,
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[period-keyword:{args.feature_mode}] samples={len(ds)}  "
        f"features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"up-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n"
        f"  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — check --keyword-csv / --keyword-meta / --prices-csv "
            f"cover {tickers}. dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_magnitude(args):
    """Name-search DataLab features -> continuous future-MAGNITUDE target (regression).

    Self-contained from CSVs: a ``ticker,keyword,period,ratio`` name-search file and
    a ``ticker,date,close,volume`` price file (volume needed for the volume target).
    """
    from datetime import date

    from .magnitude_dataset import load_magnitude_dataset

    if not args.prices_csv:
        raise SystemExit(
            "--prices-csv (ticker,date,close,volume) is required for --task magnitude"
        )
    raw = [t.strip() for t in args.tickers.split(",") if t.strip()]
    # The --tickers default is the datalab-db 3-ticker sentinel; for magnitude we
    # default to the full CSV universe (intersection of search & price tickers).
    tickers = None if raw == ["005930", "000660", "035420"] else raw
    ds = load_magnitude_dataset(
        keyword_csv=args.keyword_csv,
        prices_csv=args.prices_csv,
        tickers=tickers,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        target=args.target,
        horizon_sessions=args.horizon,
        signal_step=args.signal_step,
    )
    y_mean = ds.y.mean() if len(ds) else 0.0
    print(
        f"[magnitude:{args.target} h={args.horizon}] samples={len(ds)}  "
        f"features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"y_mean={y_mean:.3f}\n  dropped={dict(ds.dropped)}\n"
        f"  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — check --keyword-csv / --prices-csv cover the universe "
            f"and [{args.start}, {args.end}]. dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alternative-data ML model bake-off")
    parser.add_argument(
        "--source",
        choices=["synthetic", "datalab-demo", "datalab-db", "period-keyword"],
        default="synthetic",
        help="synthetic matrix, the real DataLab pipeline on demo rows, live DB, "
             "or patent-derived period-keyword features from CSVs (Stage 5)",
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
    parser.add_argument(
        "--signal-step", type=int, default=5,
        help="trading-day gap between signal dates (default 5=weekly). Raise toward "
             "--horizon for long horizons so outcome windows don't overlap and inflate IC.",
    )
    # datalab-db knobs (real data)
    parser.add_argument("--tickers", type=str, default="005930,000660,035420",
                        help="comma-separated tickers for --source datalab-db")
    parser.add_argument("--start", type=str, default="2021-01-01", help="window start (ISO)")
    parser.add_argument("--end", type=str, default="2023-12-31", help="window end (ISO)")
    parser.add_argument("--benchmark", type=str, default=None,
                        help="benchmark ticker for excess return (e.g. KOSPI 'KS11')")
    parser.add_argument("--prices-csv", type=str, default=None,
                        help="local ticker,date,close CSV for prices (skips ohlcv_data)")
    # period-keyword knobs (Stage 5)
    parser.add_argument("--keyword-csv", type=str, default="datalab_patent_keywords.csv",
                        help="ticker,keyword,period,ratio CSV from collect_datalab_for_keywords.py")
    parser.add_argument("--keyword-meta", type=str, default="kw_out/patent_keywords_*.json",
                        help="comma-separated paths/globs of per-ticker keyword meta JSON "
                             "(supplies first_avail_date for the point-in-time gate)")
    parser.add_argument("--feature-mode", type=str, default="period_keyword",
                        choices=["period_keyword", "fixed_keyword"],
                        help="period_keyword gates by first_avail_date; fixed_keyword is the "
                             "no-gate control")
    # task switch: direction (classification) vs magnitude (regression)
    parser.add_argument(
        "--task", type=str, default="direction", choices=["direction", "magnitude"],
        help="direction = predict up/down (classifiers); magnitude = predict future "
             "volatility/volume (regressors, --source ignored, uses name-search CSVs)",
    )
    parser.add_argument(
        "--target", type=str, default="volatility", choices=["volatility", "volume"],
        help="magnitude target: forward realized volatility or forward abnormal volume",
    )
    parser.add_argument("--csv", type=str, default=None, help="write full metrics CSV here")
    args = parser.parse_args(argv)

    if args.task == "magnitude":
        X, y, excess, dates = _load_magnitude(args)
    elif args.source == "period-keyword":
        X, y, excess, dates = _load_period_keyword(args)
    elif args.source == "datalab-db":
        X, y, excess, dates = _load_datalab_db(args)
    elif args.source == "datalab-demo":
        X, y, excess, dates = _load_datalab_demo(args)
    else:
        X, y, excess, dates = _load_synthetic(args)

    reports = run_bakeoff(
        X, y, excess, dates, n_folds=args.folds, seed=args.seed, task=args.task
    )
    print(render_table(reports, task=args.task))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            fh.write(render_csv(reports, task=args.task))
        print(f"\n[csv] wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
