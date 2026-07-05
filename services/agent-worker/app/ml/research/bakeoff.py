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
            evaluate_model(name, model, X, y, excess_returns, folds, dates)
        )
    return reports


def run_permutation(
    X: np.ndarray,
    y: np.ndarray,
    excess_returns: np.ndarray,
    dates: np.ndarray,
    *,
    n_perm: int = 200,
    n_folds: int = 5,
    seed: int = 42,
    exclude: set[str] | None = None,
) -> list[tuple[str, float, float, float]]:
    """Label-shuffle permutation test of rank-IC significance, per model.

    For each model we compute the observed walk-forward cross-sectional rank-IC
    (``rank_ic_xs`` — the per-date metric we judge on), then rebuild a null
    distribution by shuffling (y, excess_returns) together ``n_perm`` times and
    re-evaluating on the SAME folds. The one-sided p-value is the fraction of the
    null at least as large as observed. Returns (name, observed, p_value, null_mean),
    sorted by p-value. Compare p against a multiple-testing-corrected threshold
    (Bonferroni or Benjamini-Hochberg over models × horizons) before calling
    anything a signal.

    ``exclude`` drops models from the test by name — use it for O(n^3) estimators
    (``gaussian_process``) that are impractical to permute at panel scale.
    """
    folds = walk_forward_folds(dates, n_folds=n_folds)
    registry = build_classifier_registry(seed=seed)
    if exclude:
        registry = {k: v for k, v in registry.items() if k not in exclude}
    rng = np.random.default_rng(seed)
    n = len(y)
    results: list[tuple[str, float, float, float]] = []
    for name, model in registry.items():
        obs = evaluate_model(
            name, model, X, y, excess_returns, folds, dates
        )._agg("rank_ic_xs")[0]
        if np.isnan(obs):
            results.append((name, obs, float("nan"), float("nan")))
            continue
        null: list[float] = []
        for _ in range(n_perm):
            p = rng.permutation(n)
            r = evaluate_model(
                name, model, X, y[p], excess_returns[p], folds, dates
            )._agg("rank_ic_xs")[0]
            if not np.isnan(r):
                null.append(r)
        arr = np.array(null)
        pval = float((arr >= obs).mean()) if len(arr) else float("nan")
        results.append((name, obs, pval, float(arr.mean()) if len(arr) else float("nan")))
    results.sort(key=lambda t: (t[2] if t[2] == t[2] else 1.0))
    return results


def render_permutation(results, n_perm: int, n_models: int, n_tests: int = 0) -> str:
    """Pretty-print the permutation table with Bonferroni AND Benjamini-Hochberg refs.

    The within-table BH is computed over THIS run's models; for the full family use
    the cross-horizon aggregator on the ``--permute-csv`` rows (the proper scope).
    """
    from .stats import benjamini_hochberg

    pvals = [pval for _, _, pval, _ in results]
    bh = benjamini_hochberg(pvals, alpha=0.05)
    lines = [f"\n[permutation] n_perm={n_perm} per model - one-sided p(rankIC)"]
    lines.append(
        f"  {'model':>18}  {'obs_rankIC':>10}  {'null_mean':>9}  {'p-value':>7}  "
        f"{'BH_q':>6}  {'BH?':>3}"
    )
    for (name, obs, pval, null_mean), q, rej in zip(results, bh.qvalues, bh.rejected):
        lines.append(
            f"  {name:>18}  {obs:>+10.3f}  {null_mean:>+9.3f}  {pval:>7.3f}  "
            f"{q:>6.3f}  {'YES' if rej else '':>3}"
        )
    thr = 0.05 / max(1, (n_tests or n_models))
    lines.append(f"  (Bonferroni ref: 0.05 / {n_tests or n_models} ~= {thr:.4f}; "
                 f"BH rejected={bh.n_rejected} within this run)")
    return "\n".join(lines)


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


def _load_hiring_db(args):
    """Load a real HIRING dataset from the DB (needs DATABASE_URL + --prices-csv)."""
    import asyncio
    import os
    from datetime import date

    from .hiring_db import load_from_env

    try:  # let a repo-root .env supply DATABASE_URL without manual export
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source hiring-db")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    # hiring feature sets: volume | duty | volume+duty. The shared --feature-set
    # default is 'all' (a patent term) -> treat as the hiring default 'volume'.
    fs = getattr(args, "feature_set", "all")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume"
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
            min_observations=args.min_obs,
            precise_rematch=args.precise_rematch,
            feature_set=fs,
            target=getattr(args, "target", "direction"),
            min_cross_section=getattr(args, "min_cross_section", 6),
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[hiring-db] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"up-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — is 2016-2023 HIRING loaded for these tickers and "
            f"--prices-csv valid? dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_patent_db(args):
    """Load a real PATENT dataset from the DB (needs DATABASE_URL + loaded/enriched patents)."""
    import asyncio
    import os
    from datetime import date

    from .patent_db import load_from_env

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source patent-db")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    # 'count' drops the LLM significance trio (mostly-empty -> median-imputed into a
    # stock-identity proxy) and the near-constant new-category count.
    _COUNT_ONLY_DROP = frozenset(
        {
            "patent__llm_enriched_count",
            "patent__mean_significance",
            "patent__max_significance",
            "patent__new_category_count",
        }
    )
    exclude = _COUNT_ONLY_DROP if getattr(args, "feature_set", "all") == "count" else frozenset()
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
            xs_normalize=getattr(args, "xs_normalize", "none"),
            exclude_features=exclude,
            target=getattr(args, "target", "direction"),
            min_cross_section=getattr(args, "min_cross_section", 6),
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[patent-db] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"up-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — are patents loaded+enriched (publication_date) for these tickers? "
            f"dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_fusion(args):
    """Load a multi-source FUSION dataset (patent+hiring+datalab joined on stock-date)."""
    import asyncio
    import os
    from datetime import date

    from .fusion_db import load_fusion

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source fusion")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    sources = [s.strip() for s in args.fusion_sources.split(",") if s.strip()]
    ds = asyncio.run(
        load_fusion(
            database_url=database_url,
            tickers=tickers,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            sources=sources,
            benchmark_ticker=args.benchmark,
            prices_csv=args.prices_csv,
            lookback_days=args.lookback,
            horizon_sessions=args.horizon,
            neutral_band_pct=args.band,
            signal_step=args.signal_step,
            xs_normalize=getattr(args, "xs_normalize", "rank"),
            min_sources=getattr(args, "fusion_min_sources", None),
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[fusion {'+'.join(sources)}] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"dates={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"up-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No joined samples — do all requested sources share (stock,date) rows? "
            f"Check signal-step/window. dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_revenue(args):
    """매출 나우캐스팅: prod 채용 공고 + 연구 매출 CSV → 분기 YoY 성장 횡단면 라벨."""
    import asyncio
    import os

    from .fundamentals_dataset import load_from_env

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source revenue")
    if not args.revenue_csv:
        raise SystemExit("--revenue-csv is required for --source revenue "
                         "(run app.ml.research.fundamentals_dart first)")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    fs = getattr(args, "feature_set", "all")
    if fs not in ("volume", "duty", "volume+duty"):
        fs = "volume"
    ds = asyncio.run(
        load_from_env(
            database_url=database_url,
            revenue_csv=args.revenue_csv,
            tickers=tickers,
            lookback_days=args.lookback,
            feature_set=fs,
            min_observations=args.min_obs,
            min_cross_section=args.min_cross_section,
            precise_rematch=args.precise_rematch,
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[revenue] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"quarters={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"hi-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — is revenue_csv populated and HIRING loaded for these tickers? "
            f"dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def _load_patent_revenue(args):
    """특허→차기분기 매출 나우캐스팅: prod 특허 + 연구 매출 CSV → 분기 YoY 성장 횡단면 라벨."""
    import asyncio
    import os

    from .fundamentals_dataset import load_patent_revenue_from_env

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required for --source patent-revenue")
    if not args.revenue_csv:
        raise SystemExit("--revenue-csv is required for --source patent-revenue "
                         "(run app.ml.research.fundamentals_dart first)")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    ds = asyncio.run(
        load_patent_revenue_from_env(
            database_url=database_url,
            revenue_csv=args.revenue_csv,
            tickers=tickers,
            lookback_days=args.lookback,
            min_observations=getattr(args, "min_obs", 1),
            min_cross_section=args.min_cross_section,
            xs_normalize=getattr(args, "xs_normalize", "none"),
        )
    )
    up_rate = ds.y.mean() if len(ds) else 0.0
    print(
        f"[patent-revenue] samples={len(ds)}  features={len(ds.feature_names)}  "
        f"stocks={len(np.unique(ds.stock_ids)) if len(ds) else 0}  "
        f"quarters={len(np.unique(ds.dates)) if len(ds) else 0}  "
        f"hi-rate={up_rate:.2f}\n  dropped={dict(ds.dropped)}\n  features={ds.feature_names}\n"
    )
    if len(ds) == 0:
        raise SystemExit(
            "No samples built — is revenue_csv populated and patents loaded for these tickers? "
            f"dropped={dict(ds.dropped)}"
        )
    return ds.X, ds.y, ds.excess_returns, ds.dates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alternative-data ML model bake-off")
    parser.add_argument(
        "--source",
        choices=["synthetic", "datalab-demo", "datalab-db", "patent-db", "hiring-db",
                 "fusion", "revenue", "patent-revenue"],
        default="synthetic",
        help="synthetic, DataLab demo/DB, Patent DB, Hiring DB, multi-source fusion, "
             "revenue nowcasting (hiring->next-quarter revenue), or patent-revenue "
             "(patent features -> next-quarter revenue growth)",
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
    # hiring-db knobs (sparse, event-like data)
    parser.add_argument("--signal-step", type=int, default=20,
                        help="trading-day spacing between signal dates (hiring-db; ~20=monthly, curbs overlap)")
    parser.add_argument("--min-obs", type=int, default=2,
                        help="min postings in the lookback window to keep a sample (hiring-db)")
    parser.add_argument("--precise-rematch", action="store_true",
                        help="hiring-db: re-attribute postings by exact source_name match to the "
                             "universe (precision; drops ambiguous/mis-attributed names)")
    parser.add_argument("--target",
                        choices=["direction", "abs_return", "realized_vol"],
                        default="direction",
                        help="hiring-db: prediction target — direction (up/down, default), or a "
                             "MAGNITUDE target: abs_return (|excess|) / realized_vol (forward vol). "
                             "Magnitude uses per-date cross-sectional big/small labels.")
    parser.add_argument("--min-cross-section", type=int, default=6,
                        help="hiring-db magnitude: min stocks per date to keep that date's "
                             "cross-section (thin dates dropped; default 6)")
    parser.add_argument("--permute", type=int, default=0,
                        help="permutation test: label-shuffle iterations per model for a "
                             "rank-IC p-value (0=off; ~200 for a confirmatory run)")
    parser.add_argument("--permute-tests", type=int, default=0,
                        help="total comparisons for the Bonferroni/BH reference (e.g. models×horizons); "
                             "0 = use model count")
    parser.add_argument("--permute-exclude", type=str, default="",
                        help="comma-separated model names to skip in the permutation test "
                             "(e.g. 'gaussian_process' — O(n^3), impractical at panel scale)")
    parser.add_argument("--permute-csv", type=str, default=None,
                        help="append permutation rows (horizon,model,obs_rankIC,p_value,null_mean) "
                             "here for cross-horizon BH-FDR aggregation")
    # patent-db / hiring-db knobs (cross-sectional hygiene; feature selection)
    parser.add_argument("--xs-normalize", choices=["none", "rank", "zscore"], default="none",
                        help="patent-db: neutralize size-confounded count features within each "
                             "date (rank=percentile, zscore). Prevents models reading stock size.")
    parser.add_argument("--feature-set",
                        choices=["all", "count", "count+llm", "volume", "duty", "volume+duty"],
                        default="all",
                        help="patent-db: 'count'/'count+llm'. hiring-db: 'volume' (default), 'duty' "
                             "(tech-mix only), or 'volume+duty'.")
    parser.add_argument("--fusion-sources", type=str, default="patent,hiring,datalab",
                        help="comma-separated sources to join for --source fusion")
    parser.add_argument("--fusion-min-sources", type=int, default=None,
                        help="fusion: keep stock-dates present in >= K sources (k-of-n outer "
                             "join; missing source cols -> NaN). Default None = inner join "
                             "(all sources). Use e.g. 2 with 3 sources for a recall variant.")
    parser.add_argument("--revenue-csv", type=str, default=None,
                        help="revenue CSV (app.ml.research.fundamentals_dart output) for "
                             "--source revenue")
    parser.add_argument("--csv", type=str, default=None, help="write full metrics CSV here")
    args = parser.parse_args(argv)

    if args.source == "revenue":
        X, y, excess, dates = _load_revenue(args)
    elif args.source == "patent-revenue":
        X, y, excess, dates = _load_patent_revenue(args)
    elif args.source == "fusion":
        X, y, excess, dates = _load_fusion(args)
    elif args.source == "hiring-db":
        X, y, excess, dates = _load_hiring_db(args)
    elif args.source == "patent-db":
        X, y, excess, dates = _load_patent_db(args)
    elif args.source == "datalab-db":
        X, y, excess, dates = _load_datalab_db(args)
    elif args.source == "datalab-demo":
        X, y, excess, dates = _load_datalab_demo(args)
    else:
        X, y, excess, dates = _load_synthetic(args)

    reports = run_bakeoff(X, y, excess, dates, n_folds=args.folds, seed=args.seed)
    print(render_table(reports))

    if args.permute > 0:
        exclude = {s.strip() for s in args.permute_exclude.split(",") if s.strip()}
        perm = run_permutation(
            X, y, excess, dates, n_perm=args.permute, n_folds=args.folds,
            seed=args.seed, exclude=exclude,
        )
        print(render_permutation(perm, args.permute, len(perm), args.permute_tests))
        if args.permute_csv:
            import csv as _csv
            import os as _os
            new = not _os.path.exists(args.permute_csv)
            with open(args.permute_csv, "a", newline="", encoding="utf-8") as fh:
                w = _csv.writer(fh)
                if new:
                    w.writerow(["horizon", "model", "obs_rankIC", "p_value", "null_mean"])
                for name, obs, pval, null_mean in perm:
                    w.writerow([args.horizon, name, f"{obs:.6f}", f"{pval:.6f}",
                                f"{null_mean:.6f}"])
            print(f"[permute-csv] appended {len(perm)} rows -> {args.permute_csv}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            fh.write(render_csv(reports))
        print(f"\n[csv] wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
