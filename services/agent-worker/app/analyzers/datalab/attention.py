"""attention_spike — a NEUTRAL search-attention magnitude flag.

The directional hypothesis (search volume → price direction) was rejected
end-to-end. The one robust survivor is search → *magnitude*: an abnormal search
spike precedes elevated future trading volume / volatility (research IC +0.37,
t≈41, all eras & both markets). This module turns that into a point-in-time,
non-directional "주목/주의" tier with grounded evidence — never a buy/sell call.

Pure functions, no clock, no DB. The abnormal-z formula (trailing-60 population
stdev, ≥30 prior points, past-only) is ported verbatim from the research script
``scripts/search_to_magnitude.py::rolling_z`` so runtime matches calibration.

See ``docs/attention-spike-flag-design.md`` for the design and tier table.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.analyzers.config import DataLabRuleConfig

AttentionTier = Literal["정상", "주의", "주목", "급증"]

# Tier → risk_flag is the same neutral marker for every spike tier; the tier
# nuance (and any calibrated multipliers) lives in ``evidence_text``. The flag is
# deliberately NOT registered as a confidence penalty — attention is neutral.
ATTENTION_FLAG = "attention_spike"


@dataclass(frozen=True)
class AttentionSpike:
    attention_z: float
    attention_tier: AttentionTier
    expected_fwd_vol_mult: float | None
    expected_fwd_volume_mult: float | None
    evidence_text: str
    # Set for 주의/주목/급증; None for 정상 (no caution to surface).
    risk_flag: str | None


def _parse_series(series, as_of: date) -> list[tuple[date, float]]:
    """Normalize the loader's PIT search series to sorted ``(date, value)`` pairs
    on or before ``as_of`` (drops future-dated points — look-ahead guard)."""
    pairs: list[tuple[date, float]] = []
    items = series.items() if isinstance(series, dict) else series
    for entry in items:
        raw_date, raw_value = entry[0], entry[1]
        if raw_date is None or raw_value is None:
            continue
        d = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10])
        if d > as_of:
            continue
        pairs.append((d, float(raw_value)))
    pairs.sort(key=lambda p: p[0])
    return pairs


def _tier_and_multipliers(
    z: float, config: DataLabRuleConfig
) -> tuple[AttentionTier, str | None, float | None, float | None]:
    if z >= config.attention_z_surge:
        return "급증", ATTENTION_FLAG, config.attention_vol_mult_surge, config.attention_volume_mult_surge
    if z >= config.attention_z_watch:
        return "주목", ATTENTION_FLAG, config.attention_vol_mult_watch, config.attention_volume_mult_watch
    if z >= config.attention_z_caution:
        return "주의", ATTENTION_FLAG, config.attention_vol_mult_caution, config.attention_volume_mult_caution
    return "정상", None, None, None


def _evidence_text(
    z: float, tier: AttentionTier, vol_mult: float | None, volume_mult: float | None
) -> str:
    if tier == "정상":
        return f"검색 어텐션 정상 범위(z {z:.1f}). 특이 신호 없음."
    if vol_mult is not None and volume_mult is not None:
        return (
            f"검색 급증(z {z:.1f}, {tier}) — 향후 5일 거래량 평소 {volume_mult:.1f}배·"
            f"변동성 {vol_mult:.1f}배 예상. 방향 아님, 주의 신호."
        )
    return (
        f"검색 급증(z {z:.1f}, {tier} 등급) — 향후 거래량·변동성 증가 예상. "
        "방향 아님, 주의 신호."
    )


def compute_attention_spike(
    series,
    *,
    as_of: date,
    config: DataLabRuleConfig,
) -> AttentionSpike | None:
    """Abnormal-z of the latest search point vs its trailing baseline → tier.

    ``series`` is the loader's PIT blended daily search index — a dict
    ``{iso_date: value}`` or an iterable of ``(date, value)`` pairs. Returns
    ``None`` when there is not enough prior history (< ``attention_min_history``)
    to trust the z — the caller then surfaces nothing (silent skip).

    Look-ahead is impossible: only points strictly before the latest observation
    feed mean/stdev, and any point dated after ``as_of`` is dropped upstream.
    """
    pairs = _parse_series(series, as_of)
    if len(pairs) < config.attention_min_history + 1:
        return None

    latest_value = pairs[-1][1]
    prior = [value for _, value in pairs[:-1]]
    if len(prior) < config.attention_min_history:
        return None

    window = prior[-config.attention_window :]
    mu = statistics.mean(window)
    sd = statistics.pstdev(window)  # population stdev — matches research rolling_z
    z = (latest_value - mu) / sd if sd > 0 else 0.0

    tier, flag, vol_mult, volume_mult = _tier_and_multipliers(z, config)
    return AttentionSpike(
        attention_z=z,
        attention_tier=tier,
        expected_fwd_vol_mult=vol_mult,
        expected_fwd_volume_mult=volume_mult,
        evidence_text=_evidence_text(z, tier, vol_mult, volume_mult),
        risk_flag=flag,
    )
