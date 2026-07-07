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
    # 최신 이벤트(공개일 폴백 출원일)와 그로부터 경과일. 이름은 하위호환으로 유지하되
    # 값은 공개일 기준(_event_date). stale 판정이 "최근 공개가 얼마나 오래됐나"를 의미.
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
        # 이벤트(정보 노출) 시점 = 공개일. 특허는 출원 후 ~18개월 뒤 공개되므로
        # 최근성/모멘텀은 공개일 기준으로 버킷팅한다(공개일 미상이면 출원일 폴백).
        applied = _event_date(row)
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


def _event_date(row: dict) -> date | None:
    """정보 노출 시점 = 공개일(publication_date). 미상이면 출원일(application_date) 폴백."""
    return _parse_date(row.get("publication_date")) or _parse_date(row.get("application_date"))


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
