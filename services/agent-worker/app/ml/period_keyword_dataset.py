"""Build a bake-off Dataset from patent-derived PERIOD KEYWORDS' DataLab series.

Stage 5 of the patent -> keyword -> DataLab experiment. The earlier finding was
that DataLab search volume on a FIXED set of category keywords carries no return
signal. This builder tests the remaining hypothesis: does measuring the search
interest in the keywords that were ACTUALLY TRENDING at each point in time
(extracted from patent titles published by then) recover a signal the fixed path
missed?

Two honest-backtest guards, identical in spirit to ``datalab_dataset``:

1. Features at signal date ``as_of`` use only weekly ratios observed ``<= as_of``
   (within the lookback window) AND — in ``period_keyword`` mode — only keywords
   whose patent first became public on/before ``as_of`` (``first_avail_date <=
   as_of``). The keyword set is therefore point-in-time honest: nothing that was
   not yet knowable can enter a feature.
2. The label uses only prices STRICTLY AFTER ``as_of`` (reused ``make_label`` /
   ``PriceSeries.forward_return_pct``).

The ``fixed_keyword`` mode is the experiment's CONTROL: it drops guard (1)'s
keyword gate so every keyword is active on every date. Same keywords, same search
series, same prices, same labels — only the point-in-time TIMING differs. If
``period_keyword`` cannot beat ``fixed_keyword``, the pivot to time-appropriate
keywords did not rescue the signal.

DB-free: keyword ratios come from ``collect_datalab_for_keywords.py``'s CSV
(``ticker,keyword,period,ratio``) and the per-ticker meta JSON
(``first_avail_date``), prices from a local ``ticker,date,close`` CSV. Pure Python
+ numpy, so the whole transform is deterministic and unit-testable without a DB.
"""

from __future__ import annotations

import csv
import glob
import json
from collections import Counter
from datetime import date, timedelta

import numpy as np

from .datalab_dataset import Dataset, PriceSeries, _as_date, weekly_signal_dates
from .features import build_feature_row, feature_matrix
from .labels import make_label

# (ticker, keyword) identifies one collected weekly search series.
KeyT = tuple[str, str]

FEATURE_MODES = ("period_keyword", "fixed_keyword")


# --------------------------------------------------------------------------- #
# Loaders (CSV ratio series + JSON keyword meta)
# --------------------------------------------------------------------------- #
def load_keyword_series(csv_path: str) -> dict[KeyT, list[tuple[date, float]]]:
    """Return ``{(ticker, keyword): [(week_date, ratio), ...]}`` from the collect CSV.

    Columns: ``ticker,keyword,period,ratio`` where ``period`` is a weekly ISO date.
    Each series is sorted ascending so windowing/`[-1]` mean "most recent".
    """
    series: dict[KeyT, list[tuple[date, float]]] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        _require_columns(reader.fieldnames, csv_path, {"ticker", "keyword", "period", "ratio"})
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            keyword = (row.get("keyword") or "").strip()
            d = _as_date(row.get("period"))
            ratio = _to_float(row.get("ratio"))
            if not ticker or not keyword or d is None or ratio is None:
                continue
            series.setdefault((ticker, keyword), []).append((d, ratio))
    for key in series:
        series[key].sort(key=lambda p: p[0])
    return series


def load_keyword_meta(meta_paths: list[str]) -> dict[KeyT, date]:
    """Return ``{(ticker, keyword): first_avail_date}`` = EARLIEST availability.

    A keyword can surface in several periods; the first time its patent became
    public is the only date that matters for the point-in-time gate, so we keep
    the minimum ``first_avail_date`` across all entries for that (ticker, keyword).
    """
    first: dict[KeyT, date] = {}
    for path in meta_paths:
        with open(path, encoding="utf-8") as fh:
            items = json.load(fh)
        for item in items:
            ticker = str(item.get("ticker", "")).strip()
            keyword = str(item.get("keyword", "")).strip()
            d = _as_date(item.get("first_avail_date"))
            if not ticker or not keyword or d is None:
                continue
            key = (ticker, keyword)
            if key not in first or d < first[key]:
                first[key] = d
    return first


def resolve_meta_paths(spec: str) -> list[str]:
    """Expand a comma-separated list of file paths and/or globs into real paths."""
    paths: list[str] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        matched = sorted(glob.glob(part))
        paths.extend(matched or [part])
    return paths


# --------------------------------------------------------------------------- #
# Feature builder (set-invariant aggregates over the eligible keyword set)
# --------------------------------------------------------------------------- #
def _window(points: list[tuple[date, float]], *, as_of: date, lookback_days: int) -> list[float]:
    """Ratios observed in ``(as_of - lookback, as_of]``, ascending (leakage guard)."""
    lo = as_of - timedelta(days=lookback_days)
    return [v for d, v in points if lo < d <= as_of]


def keyword_feature_row(
    *,
    ticker: str,
    as_of: date,
    series_by_kw: dict[KeyT, list[tuple[date, float]]],
    first_avail: dict[KeyT, date],
    lookback_days: int,
    pit_gate: bool,
    spike_mult: float = 1.5,
) -> dict[str, float] | None:
    """Aggregate the eligible keywords' recent search behaviour into one feature row.

    Because the eligible keyword SET changes from date to date (new tech keeps
    appearing), features must be set-invariant aggregates — never per-keyword
    columns — so the schema is stable across time. Returns ``None`` when no keyword
    is both eligible and has any ratio in the window (caller drops the sample).
    """
    levels: list[float] = []
    moms: list[float] = []
    spikes = 0
    n_active = 0
    for (kw_ticker, keyword), points in series_by_kw.items():
        if kw_ticker != ticker:
            continue
        if pit_gate:
            avail = first_avail.get((kw_ticker, keyword))
            if avail is None or avail > as_of:  # patent not public yet -> not knowable
                continue
        window = _window(points, as_of=as_of, lookback_days=lookback_days)
        if not window:
            continue
        n_active += 1
        levels.append(window[-1])
        if len(window) >= 2:
            prior = sum(window[:-1]) / len(window[:-1])
            recent = window[-1]
            moms.append((recent - prior) / (abs(prior) + 1e-9))
            if recent > spike_mult * prior:
                spikes += 1
    if n_active == 0:
        return None
    mom_values = moms or [0.0]
    mapping = {
        "n_active": float(n_active),
        "mean_level": sum(levels) / len(levels),
        "mean_momentum": sum(mom_values) / len(mom_values),
        "max_momentum": max(mom_values),
        "breadth": (sum(1 for m in moms if m > 0) / len(moms)) if moms else 0.0,
        "spike_count": float(spikes),
    }
    return build_feature_row("period_keyword", mapping)


# --------------------------------------------------------------------------- #
# Dataset assembly (mirrors datalab_dataset.build_dataset)
# --------------------------------------------------------------------------- #
def build_period_keyword_dataset(
    *,
    series_by_kw: dict[KeyT, list[tuple[date, float]]],
    first_avail: dict[KeyT, date],
    prices_by_ticker: dict[str, PriceSeries],
    signal_dates_by_ticker: dict[str, list[date]],
    benchmark: PriceSeries | None = None,
    feature_mode: str = "period_keyword",
    lookback_days: int = 30,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    spike_mult: float = 1.5,
) -> Dataset:
    """Assemble per-(ticker, signal-date) keyword feature rows and forward-return labels.

    ``feature_mode='period_keyword'`` gates keywords by ``first_avail_date``;
    ``'fixed_keyword'`` disables that gate (the control). Every drop is counted in
    ``Dataset.dropped`` so a tiny surviving sample can never pass silently.
    """
    if feature_mode not in FEATURE_MODES:
        raise ValueError(f"feature_mode must be one of {FEATURE_MODES}, got {feature_mode!r}")
    pit_gate = feature_mode == "period_keyword"

    # Stable integer codes for reporting; the walk-forward splitter only uses dates.
    ticker_code = {t: i for i, t in enumerate(sorted(signal_dates_by_ticker))}

    feat_rows: list[dict[str, float]] = []
    y: list[int] = []
    excess: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for ticker, signal_dates in signal_dates_by_ticker.items():
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        for as_of in signal_dates:
            row = keyword_feature_row(
                ticker=ticker,
                as_of=as_of,
                series_by_kw=series_by_kw,
                first_avail=first_avail,
                lookback_days=lookback_days,
                pit_gate=pit_gate,
                spike_mult=spike_mult,
            )
            if row is None:
                dropped["no_active_keyword"] += 1
                continue
            stock_ret = prices.forward_return_pct(as_of, horizon_sessions)
            if stock_ret is None:
                dropped["no_forward_price"] += 1
                continue
            bench_ret = (
                benchmark.forward_return_pct(as_of, horizon_sessions)
                if benchmark is not None
                else 0.0
            )
            if bench_ret is None:
                bench_ret = 0.0  # benchmark gap -> fall back to raw return
            label = make_label(
                stock_return_pct=stock_ret,
                benchmark_return_pct=bench_ret,
                neutral_band_pct=neutral_band_pct,
            )
            if label.y_direction is None:
                dropped["neutral_band"] += 1
                continue
            feat_rows.append(row)
            y.append(label.y_direction)
            excess.append(label.excess_return_pct)
            dates.append(as_of.toordinal())
            stock_ids.append(ticker_code[ticker])

    matrix, names = feature_matrix(feat_rows)
    return Dataset(
        X=np.array(matrix, dtype=float).reshape(len(feat_rows), len(names)),
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=np.array(dates, dtype=int),
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=names,
        dropped=dropped,
    )


def load_period_keyword_dataset(
    *,
    keyword_csv: str,
    meta_paths: list[str],
    prices_csv: str,
    tickers: list[str],
    start: date,
    end: date,
    benchmark_ticker: str | None = None,
    feature_mode: str = "period_keyword",
    lookback_days: int = 30,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    signal_step: int = 5,
    spike_mult: float = 1.5,
) -> Dataset:
    """Load keyword series + meta + local prices and build the Dataset (no DB).

    Signal dates are the trading days in ``[start, end]`` sampled every
    ``signal_step`` sessions (weekly by default) — mirroring the DataLab path so
    the two experiments are compared on the same sampling cadence.
    """
    from .prices_csv import load_prices_csv

    series_by_kw = load_keyword_series(keyword_csv)
    first_avail = load_keyword_meta(meta_paths)
    prices_by_ticker = load_prices_csv(prices_csv)
    benchmark = prices_by_ticker.get(benchmark_ticker) if benchmark_ticker else None

    signal_dates_by_ticker: dict[str, list[date]] = {}
    prices_subset: dict[str, PriceSeries] = {}
    for ticker in tickers:
        prices = prices_by_ticker.get(ticker)
        if prices is None:
            signal_dates_by_ticker[ticker] = []
            continue
        prices_subset[ticker] = prices
        in_window = [d for d in prices.dates if start <= d <= end]
        signal_dates_by_ticker[ticker] = weekly_signal_dates(in_window, step=signal_step)

    return build_period_keyword_dataset(
        series_by_kw=series_by_kw,
        first_avail=first_avail,
        prices_by_ticker=prices_subset,
        signal_dates_by_ticker=signal_dates_by_ticker,
        benchmark=benchmark,
        feature_mode=feature_mode,
        lookback_days=lookback_days,
        horizon_sessions=horizon_sessions,
        neutral_band_pct=neutral_band_pct,
        spike_mult=spike_mult,
    )


def _require_columns(fieldnames, path: str, needed: set[str]) -> None:
    have = {(f or "").strip() for f in (fieldnames or [])}
    missing = needed - have
    if missing:
        raise ValueError(f"{path} is missing columns {sorted(missing)}; got {sorted(have)}")


def _to_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "FEATURE_MODES",
    "load_keyword_series",
    "load_keyword_meta",
    "resolve_meta_paths",
    "keyword_feature_row",
    "build_period_keyword_dataset",
    "load_period_keyword_dataset",
]
