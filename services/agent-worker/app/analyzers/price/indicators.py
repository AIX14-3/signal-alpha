"""Pure technical-indicator math over the OHLCV rows the collector provides.

Every function is deterministic and side-effect free so rule thresholds can be
tested with small fixture series. Windows that lack enough history return
``None`` instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev

MIN_SESSIONS = 21  # one volume z-score window + the day being scored
FULL_HISTORY_SESSIONS = 60  # needed for the 60-day moving-average trend read


@dataclass(frozen=True)
class PriceIndicators:
    sessions: int
    last_close: float
    sma5: float | None
    sma20: float | None
    sma60: float | None
    golden_cross: bool
    dead_cross: bool
    rsi14: float | None
    volume_z: float | None
    volatility20: float | None
    foreign_streak: int
    institution_streak: int
    foreign_net_sum20: int | None
    institution_net_sum20: int | None


def sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Cutler's RSI (simple averages) — deterministic and seed-free."""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        delta = current - previous
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    relative_strength = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + relative_strength)


def volume_zscore(volumes: list[float], window: int = 20) -> float | None:
    """Last session's volume versus the mean/stdev of the prior ``window`` days."""
    if len(volumes) < window + 1:
        return None
    baseline = volumes[-window - 1 : -1]
    mean = sum(baseline) / window
    deviation = pstdev(baseline)
    if deviation == 0:
        return None
    return (volumes[-1] - mean) / deviation


def volatility(closes: list[float], window: int = 20) -> float | None:
    """Population stdev of daily returns (%) over the trailing window."""
    if len(closes) < window + 1:
        return None
    returns = [
        (current - previous) / previous * 100.0
        for previous, current in zip(closes[-window - 1 : -1], closes[-window:])
        if previous != 0
    ]
    if len(returns) < window:
        return None
    return pstdev(returns)


def net_buy_streak(net_values: list[int | None]) -> int:
    """Signed run length of the latest net-buy direction (+3 = 3 buying days)."""
    streak = 0
    for value in reversed(net_values):
        if value is None or value == 0:
            break
        if value > 0:
            if streak < 0:
                break
            streak += 1
        else:
            if streak > 0:
                break
            streak -= 1
    return streak


def crossed(closes: list[float], short: int, long: int, lookback: int = 3) -> tuple[bool, bool]:
    """(golden, dead): did the short SMA cross the long SMA within ``lookback`` sessions?"""
    golden = False
    dead = False
    for offset in range(lookback, 0, -1):
        series_now = closes[: len(closes) - offset + 1]
        series_prev = closes[: len(closes) - offset]
        short_now, long_now = sma(series_now, short), sma(series_now, long)
        short_prev, long_prev = sma(series_prev, short), sma(series_prev, long)
        if None in (short_now, long_now, short_prev, long_prev):
            continue
        if short_prev <= long_prev and short_now > long_now:
            golden = True
        if short_prev >= long_prev and short_now < long_now:
            dead = True
    return golden, dead


def _net_sum(net_values: list[int | None], window: int = 20) -> int | None:
    recent = [value for value in net_values[-window:] if value is not None]
    if not recent:
        return None
    return sum(recent)


def compute_indicators(rows: list[dict]) -> PriceIndicators:
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    foreign = [row.get("foreign_net") for row in rows]
    institution = [row.get("institution_net") for row in rows]
    golden, dead = crossed(closes, short=5, long=20)
    return PriceIndicators(
        sessions=len(rows),
        last_close=closes[-1],
        sma5=sma(closes, 5),
        sma20=sma(closes, 20),
        sma60=sma(closes, 60),
        golden_cross=golden,
        dead_cross=dead,
        rsi14=rsi(closes),
        volume_z=volume_zscore(volumes),
        volatility20=volatility(closes),
        foreign_streak=net_buy_streak(foreign),
        institution_streak=net_buy_streak(institution),
        foreign_net_sum20=_net_sum(foreign),
        institution_net_sum20=_net_sum(institution),
    )
