"""Pure aggregation over patent filing rows.

Deterministic and side-effect free so rule thresholds can be tested with small
fixtures. The reference date (``as_of``) is supplied by the loader — this module
never reads the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PatentIndicators:
    total: int
    recent_count: int
    prior_count: int
    momentum_ratio: float | None  # (recent - prior) / prior; None when prior == 0
    new_category_count: int
    new_category_ratio: float
    distinct_tech_categories: int
    latest_application_date: str | None
    days_since_latest: int | None
    # LLM-enrichment aggregates (C3). None/0 when no patent in the window has been
    # enriched, which makes the rules fall back to the pure count-based score.
    llm_enriched_count: int
    mean_significance: float | None  # mean LLM significance over enriched filings
    max_significance: float | None


def compute_indicators(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
) -> PatentIndicators:
    total = len(rows)
    if total == 0:
        return PatentIndicators(
            total=0,
            recent_count=0,
            prior_count=0,
            momentum_ratio=None,
            new_category_count=0,
            new_category_ratio=0.0,
            distinct_tech_categories=0,
            latest_application_date=None,
            days_since_latest=None,
            llm_enriched_count=0,
            mean_significance=None,
            max_significance=None,
        )

    midpoint = as_of - _half(lookback_days)
    recent_count = 0
    prior_count = 0
    new_category_count = 0
    tech_categories: set[str] = set()
    latest: date | None = None
    significances: list[float] = []

    for row in rows:
        applied = _parse_date(row.get("application_date"))
        if applied is not None:
            if applied > midpoint:
                recent_count += 1
            else:
                prior_count += 1
            if latest is None or applied > latest:
                latest = applied
        if row.get("is_new_category"):
            new_category_count += 1
        tech = row.get("tech_category")
        if tech:
            tech_categories.add(str(tech))
        significance = row.get("significance")
        if significance is not None:
            significances.append(float(significance))

    momentum_ratio = (
        (recent_count - prior_count) / prior_count if prior_count > 0 else None
    )
    mean_significance = sum(significances) / len(significances) if significances else None
    max_significance = max(significances) if significances else None
    return PatentIndicators(
        total=total,
        recent_count=recent_count,
        prior_count=prior_count,
        momentum_ratio=momentum_ratio,
        new_category_count=new_category_count,
        new_category_ratio=new_category_count / total,
        distinct_tech_categories=len(tech_categories),
        latest_application_date=latest.isoformat() if latest else None,
        days_since_latest=(as_of - latest).days if latest else None,
        llm_enriched_count=len(significances),
        mean_significance=mean_significance,
        max_significance=max_significance,
    )


def _half(lookback_days: int):
    from datetime import timedelta

    return timedelta(days=max(1, lookback_days) // 2)


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
