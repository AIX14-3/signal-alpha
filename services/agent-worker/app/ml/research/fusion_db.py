"""Multi-source fusion: join PATENT + HIRING + DataLab features on (stock, date).

Each source is individually no-signal for direction (see the patent re-verify and
DataLab/hiring reports). The fusion hypothesis is that a *combination* — e.g. a
hiring ramp + a patent burst + a search-attention move on the same name, same
week — carries residual or non-linear cross-sectional information that no single
source shows. This builder assembles the joined feature matrix so the bake-off
can test that with the same rigor (cross-sectional rank, non-overlap, permutation).

Alignment trick (why a plain join works): all three per-source loaders derive
their signal dates from the SAME ``weekly_signal_dates(price_dates, step)``. Given
identical ``prices_csv`` + ``signal_step`` + window, every source emits rows on the
same (stock_id, date) grid, with the same forward-return label. So we just inner-
join on (stock_id, date) and concatenate the source-prefixed feature columns.

Prices flow via the FDR CSV only (``ohlcv_data`` untouched).
"""

from __future__ import annotations

from datetime import date

import numpy as np
from scipy.stats import rankdata

from .datalab_dataset import Dataset


def _index_dataset(ds: Dataset) -> dict[tuple[int, int], int]:
    """Map (stock_id, date_ordinal) -> row index. Unique: one signal per stock-date."""
    return {(int(s), int(d)): i for i, (s, d) in enumerate(zip(ds.stock_ids, ds.dates))}


def _rank_all_cross_sectional(X: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """Within each date, replace every feature with its cross-sectional percentile.

    The standard rank-IC preprocessing: neutralizes scale across all sources at once
    (a patent count and a hiring count become comparable [0,1] ranks), so no single
    high-magnitude column dominates and rankIC measures pure relative standing. NaNs
    are preserved (ranked among present values only).
    """
    if X.size == 0:
        return X
    out = X.copy()
    for d in np.unique(dates):
        idx = np.where(dates == d)[0]
        if len(idx) < 2:
            continue
        for c in range(X.shape[1]):
            vals = out[idx, c]
            mask = ~np.isnan(vals)
            if mask.sum() < 2:
                continue
            vals[mask] = (rankdata(vals[mask]) - 1.0) / (mask.sum() - 1)
            out[idx, c] = vals
    return out


async def _load_one(source: str, *, database_url, tickers, start, end, benchmark_ticker,
                    prices_csv, lookback, horizon, band, signal_step) -> Dataset:
    common = dict(
        database_url=database_url, tickers=tickers, start=start, end=end,
        benchmark_ticker=benchmark_ticker, prices_csv=prices_csv,
        lookback_days=lookback, horizon_sessions=horizon, neutral_band_pct=band,
    )
    if source == "patent":
        from .patent_db import load_from_env
        return await load_from_env(signal_step=signal_step, **common)
    if source == "hiring":
        from .hiring_db import load_from_env
        return await load_from_env(signal_step=signal_step, **common)
    if source == "datalab":
        from .datalab_db import load_from_env
        return await load_from_env(signal_step=signal_step, **common)
    raise ValueError(f"unknown fusion source: {source}")


async def load_fusion(
    *,
    database_url: str,
    tickers: list[str],
    start: date,
    end: date,
    sources: list[str],
    benchmark_ticker: str | None = None,
    prices_csv: str | None = None,
    lookback_days: int = 60,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    signal_step: int = 20,
    xs_normalize: str = "rank",
) -> Dataset:
    """Inner-join the requested sources on (stock_id, date) and concatenate features."""
    per_source: dict[str, Dataset] = {}
    for src in sources:
        per_source[src] = await _load_one(
            src, database_url=database_url, tickers=tickers, start=start, end=end,
            benchmark_ticker=benchmark_ticker, prices_csv=prices_csv,
            lookback=lookback_days, horizon=horizon_sessions, band=neutral_band_pct,
            signal_step=signal_step,
        )

    indices = {src: _index_dataset(ds) for src, ds in per_source.items()}
    # Inner join: keys present in EVERY requested source.
    common_keys = set.intersection(*(set(ix.keys()) for ix in indices.values())) if indices else set()
    common_keys = sorted(common_keys)  # deterministic (stock_id, date)

    base = per_source[sources[0]]
    feature_names: list[str] = []
    for src in sources:
        feature_names.extend(per_source[src].feature_names)

    rows: list[np.ndarray] = []
    y: list[int] = []
    excess: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    for key in common_keys:
        parts = [per_source[src].X[indices[src][key]] for src in sources]
        rows.append(np.concatenate(parts))
        bi = indices[sources[0]][key]
        y.append(int(base.y[bi]))
        excess.append(float(base.excess_returns[bi]))
        stock_ids.append(key[0])
        dates.append(key[1])

    X = np.array(rows, dtype=float) if rows else np.empty((0, len(feature_names)))
    dates_arr = np.array(dates, dtype=int)
    if xs_normalize == "rank":
        X = _rank_all_cross_sectional(X, dates_arr)

    from collections import Counter

    dropped: Counter = Counter()
    for src, ds in per_source.items():
        dropped[f"{src}_rows"] = len(ds)
    dropped["joined"] = len(common_keys)

    return Dataset(
        X=X,
        y=np.array(y, dtype=int),
        excess_returns=np.array(excess, dtype=float),
        dates=dates_arr,
        stock_ids=np.array(stock_ids, dtype=int),
        feature_names=feature_names,
        dropped=dropped,
    )


__all__ = ["load_fusion"]
