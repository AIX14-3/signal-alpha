"""Non-directional (magnitude) targets for the patent-embedding research track.

The directional harness (``labels.make_label``) asks "up or down?". A patent
*content* signal may carry no directional edge yet still forecast that SOMETHING
happens — a burst of realized volatility or abnormal trading volume. Those are
the honest, testable targets for patent embeddings (see the volatility/magnitude
memo): they don't require the embedding to predict a sign it provably can't.

Two targets, both computed from prices/volumes STRICTLY AFTER ``as_of`` (entry at
the ``as_of`` close, look-ahead 0) and then WITHIN-FIRM DEMEANED:

- ``forward_realized_vol`` — std of daily returns over t+1..t+h. Magnitude of the
  move, sign-agnostic.
- ``abnormal_volume_z`` — z-score of forward mean volume vs a trailing baseline
  ending at ``as_of`` (baseline uses only past volume, so no leakage).

WITHIN-FIRM DEMEAN is the identity guard, mirroring the feature side: a big-cap's
raw volatility/volume level is a stock-identity constant. Subtracting each firm's
OWN mean target (computed on the TRAIN fold only — see ``demean_within_firm``)
leaves "unusually turbulent FOR THIS STOCK", which is what a content shock should
move. Raw levels would let a model re-learn "this is Samsung".

Pure Python + numpy; callers pass already-fetched series so this is deterministic
and unit-testable without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class VolumeSeries:
    """One stock's trading-day volumes, ascending, with O(1) date->index lookup."""

    dates: list[date]
    volumes: list[float]
    _index: dict[date, int]

    @classmethod
    def from_pairs(cls, pairs: list[tuple[date, float]]) -> "VolumeSeries":
        ordered = sorted(pairs, key=lambda p: p[0])
        dates = [d for d, _ in ordered]
        volumes = [float(v) for _, v in ordered]
        return cls(dates=dates, volumes=volumes, _index={d: i for i, d in enumerate(dates)})


def forward_realized_vol(prices, as_of: date, horizon_sessions: int) -> float | None:
    """Std of daily returns over the ``horizon_sessions`` sessions AFTER ``as_of``.

    ``prices`` is a :class:`~app.ml.research.datalab_dataset.PriceSeries` (needs
    ``dates``/``closes`` and a date index). Returns ``None`` when ``as_of`` is not a
    trading day or there aren't ``horizon_sessions + 1`` forward closes (so a label
    is never fabricated from missing data). Sign-agnostic by construction.
    """
    i = _index_of(prices, as_of)
    if i is None:
        return None
    closes = prices.closes
    # need closes at i, i+1, ..., i+h  → h daily returns
    if i + horizon_sessions >= len(closes):
        return None
    window = np.array(closes[i : i + horizon_sessions + 1], dtype=float)
    if np.any(window[:-1] == 0):
        return None
    rets = window[1:] / window[:-1] - 1.0
    return float(np.std(rets))


def abnormal_volume_z(
    volumes: VolumeSeries,
    as_of: date,
    horizon_sessions: int,
    *,
    baseline_sessions: int = 20,
) -> float | None:
    """Z-score of forward mean volume vs a trailing baseline ending at ``as_of``.

    Baseline = mean/std of the ``baseline_sessions`` volumes ENDING at ``as_of``
    (inclusive) — strictly past data, no leakage. Forward statistic = mean volume
    over t+1..t+h. Returns ``(fwd_mean - base_mean) / base_std``; ``None`` when
    ``as_of`` is not a trading day, there isn't enough past/forward data, or the
    baseline has zero variance (no scale to normalize against).
    """
    i = volumes._index.get(as_of)
    if i is None:
        return None
    vols = volumes.volumes
    if i + horizon_sessions >= len(vols):
        return None
    if i + 1 < baseline_sessions:
        return None
    base = np.array(vols[i + 1 - baseline_sessions : i + 1], dtype=float)
    fwd = np.array(vols[i + 1 : i + 1 + horizon_sessions], dtype=float)
    sd = base.std()
    if sd == 0:
        return None
    return float((fwd.mean() - base.mean()) / sd)


def demean_within_firm(
    y: np.ndarray,
    stock_ids: np.ndarray,
    *,
    fit_mask: np.ndarray | None = None,
) -> np.ndarray:
    """Subtract each firm's mean target, learning the means on ``fit_mask`` ONLY.

    ``fit_mask`` (boolean, row-aligned) marks the rows allowed to contribute to the
    per-firm mean — pass the TRAIN fold's mask so the demean statistic never sees
    test targets (leakage 0). When omitted, all rows are used (fine for a whole-
    sample descriptive demean, not for a leakage-safe fold). A firm absent from the
    fit set falls back to the global fit mean, so its rows are still centered.
    """
    y = np.asarray(y, dtype=float)
    stock_ids = np.asarray(stock_ids)
    if fit_mask is None:
        fit_mask = np.ones(len(y), dtype=bool)
    fit_mask = np.asarray(fit_mask, dtype=bool)
    fit_y = y[fit_mask]
    fit_ids = stock_ids[fit_mask]
    global_mean = float(fit_y.mean()) if fit_y.size else 0.0
    means: dict = {}
    for sid in np.unique(fit_ids):
        means[sid.item() if hasattr(sid, "item") else sid] = float(
            fit_y[fit_ids == sid].mean()
        )
    out = np.empty_like(y)
    for j, sid in enumerate(stock_ids):
        key = sid.item() if hasattr(sid, "item") else sid
        out[j] = y[j] - means.get(key, global_mean)
    return out


def _index_of(prices, as_of: date) -> int | None:
    idx = getattr(prices, "_index", None)
    if isinstance(idx, dict):
        return idx.get(as_of)
    try:
        return prices.dates.index(as_of)
    except (ValueError, AttributeError):
        return None


__all__ = [
    "VolumeSeries",
    "forward_realized_vol",
    "abnormal_volume_z",
    "demean_within_firm",
]
