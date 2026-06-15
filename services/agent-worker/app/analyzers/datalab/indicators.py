"""Pure aggregation over DataLab search-trend rows.

Rows carry a per-category ``weight`` (from ``datalab_category_stocks``) so a
stock mapped to several search themes blends them by weight. Deterministic and
clock-free; ``as_of`` is supplied by the loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class DataLabIndicators:
    observations: int
    weighted_recent_avg: float | None
    weighted_prior_avg: float | None
    momentum_pct: float | None  # DEMAND-keyword momentum (recent - prior) / prior
    spike_ratio: float
    avg_change_pct: float | None
    prior_observations: int  # demand rows in the prior window (small-sample guard input)
    # RISK-keyword momentum: rising risk searches (리콜/불매/논란…) are bearish, so the
    # rules subtract this. None when no risk keywords are mapped/collected yet.
    risk_momentum_pct: float | None
    risk_prior_observations: int
    latest_observed_date: str | None
    days_since_latest: int | None


def compute_indicators(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
) -> DataLabIndicators:
    observations = len(rows)
    if observations == 0:
        return DataLabIndicators(
            observations=0,
            weighted_recent_avg=None,
            weighted_prior_avg=None,
            momentum_pct=None,
            spike_ratio=0.0,
            avg_change_pct=None,
            prior_observations=0,
            risk_momentum_pct=None,
            risk_prior_observations=0,
            latest_observed_date=None,
            days_since_latest=None,
        )

    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)
    # DEMAND bucket (polarity demand/neutral): rising search = bullish (existing logic).
    recent_value = recent_weight = 0.0
    prior_value = prior_weight = 0.0
    spike_weight = total_weight = 0.0
    change_value = change_weight = 0.0
    prior_observations = 0
    # RISK bucket (polarity risk): rising search = bearish (subtracted in rules).
    risk_recent_value = risk_recent_weight = 0.0
    risk_prior_value = risk_prior_weight = 0.0
    risk_prior_observations = 0
    latest: date | None = None

    for row in rows:
        weight = float(row.get("weight") or 1.0)
        observed = _parse_date(row.get("observed_date"))
        search_index = row.get("search_index")
        is_risk = (row.get("polarity") or "demand") == "risk"
        if observed is not None and (latest is None or observed > latest):
            latest = observed

        if is_risk:
            if observed is not None and search_index is not None:
                value = float(search_index)
                if observed > midpoint:
                    risk_recent_value += value * weight
                    risk_recent_weight += weight
                else:
                    risk_prior_value += value * weight
                    risk_prior_weight += weight
                    risk_prior_observations += 1
            continue  # risk rows do not feed demand momentum / spike / change

        total_weight += weight
        if observed is not None and search_index is not None:
            value = float(search_index)
            if observed > midpoint:
                recent_value += value * weight
                recent_weight += weight
            else:
                prior_value += value * weight
                prior_weight += weight
                prior_observations += 1
        if row.get("is_spike"):
            spike_weight += weight
        change_pct = row.get("change_pct")
        if change_pct is not None:
            change_value += float(change_pct) * weight
            change_weight += weight

    weighted_recent_avg = recent_value / recent_weight if recent_weight > 0 else None
    weighted_prior_avg = prior_value / prior_weight if prior_weight > 0 else None
    momentum_pct = _ratio(weighted_recent_avg, weighted_prior_avg)
    risk_recent_avg = risk_recent_value / risk_recent_weight if risk_recent_weight > 0 else None
    risk_prior_avg = risk_prior_value / risk_prior_weight if risk_prior_weight > 0 else None
    risk_momentum_pct = _ratio(risk_recent_avg, risk_prior_avg)

    return DataLabIndicators(
        observations=observations,
        weighted_recent_avg=weighted_recent_avg,
        weighted_prior_avg=weighted_prior_avg,
        momentum_pct=momentum_pct,
        spike_ratio=(spike_weight / total_weight) if total_weight > 0 else 0.0,
        avg_change_pct=(change_value / change_weight) if change_weight > 0 else None,
        prior_observations=prior_observations,
        risk_momentum_pct=risk_momentum_pct,
        risk_prior_observations=risk_prior_observations,
        latest_observed_date=latest.isoformat() if latest else None,
        days_since_latest=(as_of - latest).days if latest else None,
    )


def _ratio(recent: float | None, prior: float | None) -> float | None:
    if recent is None or prior is None or prior == 0:
        return None
    return (recent - prior) / prior


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
