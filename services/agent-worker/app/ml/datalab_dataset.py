"""Build a real (X, y) dataset for the bake-off from DataLab + price rows.

This is the "plumbing" that replaces synthetic data for a DataLab-only test. It
reuses the SHIPPING feature logic (``analyzers/datalab/indicators.compute_indicators``)
and the harness label logic (``labels``), so the model sees exactly what the
production analyzer would compute — no parallel re-implementation.

Two leakage guards are non-negotiable for an honest backtest:
1. Features at a signal date ``as_of`` use only DataLab rows with
   ``observed_date <= as_of`` (and within the lookback window).
2. The label uses only prices STRICTLY AFTER ``as_of`` (entry at ``as_of`` close,
   exit ``horizon_sessions`` trading days later).

Pure Python + numpy only: callers pass already-fetched rows (see ``datalab_db``
for the DB adapter), keeping this deterministic and unit-testable without a DB.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from app.analyzers.datalab.indicators import compute_indicators

from .features import build_feature_row, feature_matrix
from .labels import excess_return_pct, make_label


@dataclass(frozen=True)
class PriceSeries:
    """One stock's trading-day closes (and optional volumes), ascending, O(1) lookup.

    ``volumes`` is parallel to ``closes`` and may be empty when only closes were
    loaded (the directional path); the forward-magnitude accessors return ``None``
    rather than guessing when volume is absent.
    """

    dates: list[date]
    closes: list[float]
    _index: dict[date, int] = field(default_factory=dict)
    volumes: list[float] = field(default_factory=list)

    @classmethod
    def from_pairs(cls, pairs: list[tuple[date, float]]) -> "PriceSeries":
        ordered = sorted(pairs, key=lambda p: p[0])
        dates = [d for d, _ in ordered]
        closes = [float(c) for _, c in ordered]
        return cls(dates=dates, closes=closes, _index={d: i for i, d in enumerate(dates)})

    @classmethod
    def from_rows(cls, rows: list[tuple[date, float, float]]) -> "PriceSeries":
        """Build from ``(date, close, volume)`` triples (the magnitude path)."""
        ordered = sorted(rows, key=lambda r: r[0])
        dates = [d for d, _, _ in ordered]
        closes = [float(c) for _, c, _ in ordered]
        volumes = [float(v) for _, _, v in ordered]
        return cls(
            dates=dates,
            closes=closes,
            _index={d: i for i, d in enumerate(dates)},
            volumes=volumes,
        )

    def forward_return_pct(self, as_of: date, horizon_sessions: int) -> float | None:
        """Percent return from ``as_of``'s close to the close ``horizon`` sessions later.

        Returns ``None`` when ``as_of`` is not a trading day in the series or there
        aren't ``horizon_sessions`` sessions of future data (so we never fabricate a
        label from missing forward prices).
        """
        i = self._index.get(as_of)
        if i is None or i + horizon_sessions >= len(self.closes):
            return None
        entry = self.closes[i]
        exit_ = self.closes[i + horizon_sessions]
        if entry == 0:
            return None
        return (exit_ / entry - 1.0) * 100.0

    def forward_closes(self, as_of: date, horizon_sessions: int) -> list[float] | None:
        """Closes ``[i .. i+h]`` (entry + h forward), for realized-volatility labels.

        Mirrors ``scripts/search_to_magnitude.fwd_vol``: returns ``None`` when
        ``as_of`` isn't a trading day or fewer than ``h`` forward sessions exist, so
        a magnitude label is never fabricated from a short window.
        """
        i = self._index.get(as_of)
        if i is None or i + horizon_sessions >= len(self.closes):
            return None
        return self.closes[i : i + horizon_sessions + 1]

    def forward_volumes(self, as_of: date, horizon_sessions: int) -> list[float] | None:
        """Volumes over the forward window ``[i+1 .. i+h]`` (strictly after ``as_of``).

        Mirrors ``search_to_magnitude.fwd_volume``'s forward window. ``None`` when
        volume is absent or fewer than ``h`` forward sessions exist.
        """
        if not self.volumes:
            return None
        i = self._index.get(as_of)
        if i is None or i + horizon_sessions >= len(self.volumes):
            return None
        return self.volumes[i + 1 : i + 1 + horizon_sessions]

    def baseline_volumes(self, as_of: date, back: int = 60) -> list[float] | None:
        """Trailing volumes ``[i-back .. i-1]`` (strictly before ``as_of``) — the baseline.

        Mirrors ``search_to_magnitude.fwd_volume``'s trailing baseline. ``None`` when
        volume is absent or there is no prior session to average.
        """
        if not self.volumes:
            return None
        i = self._index.get(as_of)
        if i is None or i <= 0:
            return None
        return self.volumes[max(0, i - back) : i]


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    excess_returns: np.ndarray
    dates: np.ndarray  # ordinal day ints, sortable by the walk-forward splitter
    stock_ids: np.ndarray
    feature_names: list[str]
    dropped: Counter  # reason -> count, for transparency on what was skipped

    def __len__(self) -> int:
        return len(self.y)


def weekly_signal_dates(trading_dates: list[date], step: int = 5) -> list[date]:
    """Every ``step``-th trading day (≈ weekly at 5), to curb autocorrelation.

    Daily signals on adjacent sessions are near-duplicates; sampling weekly gives
    more independent examples per stock.
    """
    ordered = sorted(set(trading_dates))
    return ordered[::step]


def _window_rows(rows: list[dict], *, as_of: date, lookback_days: int) -> list[dict]:
    """DataLab rows visible at ``as_of``: observed in (as_of - lookback, as_of].

    The upper bound (``<= as_of``) is the backtest leakage guard — in production
    there are no future rows, but in a historical replay there are, and they must
    not leak in.
    """
    lo = as_of - timedelta(days=lookback_days)
    out = []
    for row in rows:
        observed = row.get("observed_date")
        d = _as_date(observed)
        if d is not None and lo < d <= as_of:
            out.append(row)
    return out


def build_dataset(
    *,
    datalab_rows_by_stock: dict[int, list[dict]],
    prices_by_stock: dict[int, PriceSeries],
    signal_dates_by_stock: dict[int, list[date]],
    benchmark: PriceSeries | None = None,
    lookback_days: int = 30,
    horizon_sessions: int = 5,
    neutral_band_pct: float = 0.3,
    min_observations: int = 3,
) -> Dataset:
    """Assemble per-(stock, signal-date) feature rows and forward-return labels.

    A sample is kept only when it has enough DataLab observations to featurize AND
    a valid forward return AND a non-neutral direction; every drop is counted in
    ``Dataset.dropped`` so a tiny surviving sample can't pass silently.
    """
    feat_rows: list[dict[str, float]] = []
    y: list[int] = []
    excess: list[float] = []
    dates: list[int] = []
    stock_ids: list[int] = []
    dropped: Counter = Counter()

    for stock_id, signal_dates in signal_dates_by_stock.items():
        rows = datalab_rows_by_stock.get(stock_id, [])
        prices = prices_by_stock.get(stock_id)
        if prices is None:
            dropped["no_price_series"] += len(signal_dates)
            continue
        for as_of in signal_dates:
            window = _window_rows(rows, as_of=as_of, lookback_days=lookback_days)
            indicators = compute_indicators(
                window, as_of=as_of, lookback_days=lookback_days
            )
            if indicators.observations < min_observations:
                dropped["too_few_observations"] += 1
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
            feat_rows.append(build_feature_row("datalab", indicators))
            y.append(label.y_direction)
            excess.append(label.excess_return_pct)
            dates.append(as_of.toordinal())
            stock_ids.append(stock_id)

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


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# Re-exported so callers can build a benchmark excess label without importing
# labels directly (keeps the DataLab pipeline's public surface in one module).
__all__ = [
    "PriceSeries",
    "Dataset",
    "build_dataset",
    "weekly_signal_dates",
    "excess_return_pct",
]
