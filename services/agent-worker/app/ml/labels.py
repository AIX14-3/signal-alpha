"""Label (ground-truth) construction for the ML bake-off.

The target we predict is the *future excess return* of a signal: how much the
stock moved AFTER the signal, minus how the market moved over the same window.
Subtracting the market strips out "everything went up because the index went up",
so we measure the signal's stock-specific skill — important when history is short
and covers only one market regime.

Two RETURN label forms come out of the same return, mirroring ``backtest_results``:
- regression target ``y_return`` — the continuous excess return (signed)
- classification target ``y_direction`` — up(1)/down(0), with a NEUTRAL BAND so
  tiny moves (noise) are dropped instead of taught as signal.

A third, NON-DIRECTIONAL label form — ``MagnitudeLabel`` — predicts future
*magnitude* (realized volatility / abnormal volume) rather than the return's
sign. This is the one search-attention target that survives rigorous validation
(non-overlapping cross-sectional IC +0.15~0.30, permutation + BH-FDR q≈0 at every
horizon; the directional and search→revenue labels did not). It carries no
buy/sell meaning — see ``docs/attention-spike-flag-design.md``.

Everything here is pure Python: no numpy/sklearn, no DB, no clock. Callers pass
already-fetched numbers so this stays deterministic and unit-testable.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    """One labelled example's target, derived from realized prices.

    ``y_direction`` is ``None`` when the move falls inside the neutral band — such
    rows must be DROPPED from a classification dataset, never coerced to 0/1.
    """

    excess_return_pct: float
    y_direction: int | None  # 1=up, 0=down, None=neutral(drop)
    in_neutral_band: bool


def excess_return_pct(stock_return_pct: float, benchmark_return_pct: float) -> float:
    """Stock return minus benchmark (market or peer-average) return, in percent.

    Benchmark is whatever the caller supplies: a market index return, or — when no
    index series is available — the cross-sectional mean return of the stock
    universe on that date (a poor-man's market adjustment computable from
    ``ohlcv_data`` alone). See ``cross_sectional_excess``.
    """
    return float(stock_return_pct) - float(benchmark_return_pct)


def make_label(
    *,
    stock_return_pct: float,
    benchmark_return_pct: float,
    neutral_band_pct: float,
) -> Label:
    """Build a :class:`Label` from a realized return and its benchmark.

    ``neutral_band_pct`` is a half-width: an excess move with magnitude <= band is
    treated as neutral (dropped from classification). Use 0.0 to disable.
    """
    if neutral_band_pct < 0:
        raise ValueError("neutral_band_pct must be >= 0")
    excess = excess_return_pct(stock_return_pct, benchmark_return_pct)
    if abs(excess) <= neutral_band_pct:
        return Label(excess_return_pct=excess, y_direction=None, in_neutral_band=True)
    return Label(
        excess_return_pct=excess,
        y_direction=1 if excess > 0 else 0,
        in_neutral_band=False,
    )


@dataclass(frozen=True)
class MagnitudeLabel:
    """Non-directional future-magnitude target (the validated attention survivor).

    Both fields are UNSIGNED — they say "how much trading/movement is coming",
    never which way. ``None`` when the forward window is too short to compute.
    """

    fwd_volatility: float | None  # forward realized vol, % (stdev of daily log returns)
    fwd_abn_volume: float | None  # forward mean volume / trailing baseline mean (x)


def forward_realized_volatility(forward_closes: list[float]) -> float | None:
    """Realized volatility over the forward window = pstdev of daily log returns × 100.

    Mirrors ``scripts/search_to_magnitude.fwd_vol``. ``forward_closes`` are the
    closes at t, t+1, … t+h (already point-in-time on the caller's side). Needs at
    least 2 usable consecutive returns.
    """
    rets = [
        math.log(c1 / c0)
        for c0, c1 in zip(forward_closes, forward_closes[1:])
        if c0 > 0 and c1 > 0
    ]
    return statistics.pstdev(rets) * 100.0 if len(rets) >= 2 else None


def forward_abnormal_volume(
    forward_volumes: list[float], baseline_volumes: list[float]
) -> float | None:
    """Forward abnormal volume = mean(forward) / mean(trailing baseline).

    Mirrors ``scripts/search_to_magnitude.fwd_volume``. >1 means heavier-than-usual
    trading is coming. ``None`` when either window is empty or the baseline mean is
    non-positive.
    """
    if not forward_volumes or not baseline_volumes:
        return None
    base = statistics.mean(baseline_volumes)
    return statistics.mean(forward_volumes) / base if base > 0 else None


def make_magnitude_label(
    *,
    forward_closes: list[float],
    forward_volumes: list[float],
    baseline_volumes: list[float],
) -> MagnitudeLabel:
    """Build a :class:`MagnitudeLabel` from forward price/volume windows (PIT).

    The same definitions validated in ``search_to_magnitude`` — runtime
    ``attention_spike`` reads these multipliers from the calibration table.
    """
    return MagnitudeLabel(
        fwd_volatility=forward_realized_volatility(forward_closes),
        fwd_abn_volume=forward_abnormal_volume(forward_volumes, baseline_volumes),
    )


def cross_sectional_excess(
    returns_by_stock: dict[int, float],
) -> dict[int, float]:
    """Subtract the same-date universe mean return from each stock's return.

    Used as the benchmark when no market-index series is available: on a given
    signal date, the average move of all observed stocks approximates "the market",
    so the residual is the stock-specific excess. Returns an empty dict for empty
    input.
    """
    if not returns_by_stock:
        return {}
    mean = sum(returns_by_stock.values()) / len(returns_by_stock)
    return {stock_id: float(r) - mean for stock_id, r in returns_by_stock.items()}


def is_hit(signal_value: str, excess_return: float) -> bool:
    """Did the signal's direction match the realized excess move?

    Mirrors the ``backtest_results.is_hit`` contract: a ``positive`` signal hits on
    a positive excess return, ``negative`` on a negative one. Non-directional
    signals (``neutral``/``mixed``) never count as a hit.
    """
    if signal_value == "positive":
        return excess_return > 0
    if signal_value == "negative":
        return excess_return < 0
    return False
