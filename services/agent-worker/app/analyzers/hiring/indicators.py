"""Pure aggregation over hiring (job-postings) rows.

Deterministic and clock-free; ``as_of`` is supplied by the loader. Job-count
momentum (more openings = expansion) and the collector's ``change_pct`` are the
signal inputs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class HiringIndicators:
    observations: int
    recent_avg_job_count: float | None
    prior_avg_job_count: float | None
    momentum_pct: float | None  # (recent - prior) / prior, on seasonally-adjusted counts
    avg_change_pct: float | None
    total_job_count: int
    prior_observations: int  # rows in the prior window (small-sample guard input)
    latest_observed_date: str | None
    days_since_latest: int | None
    # Sector job-function demand (C4), precomputed by the loader from peer companies.
    # None/0 when the stock has no function mapping → analyzer omits the component.
    sector_demand_momentum: float | None = None
    sector_demand_coverage: float = 0.0
    # OCR skill enrichment (ENRICH_HIRING). 0/() when no posting in the window has
    # been OCR-enriched, which makes the rules fall back to the count-based score.
    distinct_skills: int = 0
    skill_enriched_observations: int = 0  # rows carrying ≥1 OCR skill (apply-gate)
    top_skills: tuple[str, ...] = ()


def compute_indicators(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
    sector_demand: dict | None = None,
) -> HiringIndicators:
    observations = len(rows)
    sector_momentum, sector_coverage = _sector(sector_demand)
    if observations == 0:
        return HiringIndicators(
            observations=0,
            recent_avg_job_count=None,
            prior_avg_job_count=None,
            momentum_pct=None,
            avg_change_pct=None,
            total_job_count=0,
            prior_observations=0,
            latest_observed_date=None,
            days_since_latest=None,
            sector_demand_momentum=sector_momentum,
            sector_demand_coverage=sector_coverage,
        )

    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)
    recent: list[float] = []
    prior: list[float] = []
    changes: list[float] = []
    total_job_count = 0
    latest: date | None = None
    skill_counter: Counter[str] = Counter()
    skill_enriched_observations = 0

    for row in rows:
        skills = row.get("ocr_skills") or []
        if skills:
            skill_enriched_observations += 1
            skill_counter.update(str(s) for s in skills if s)
        observed = _parse_date(row.get("observed_date"))
        job_count = row.get("job_count")
        if job_count is not None:
            total_job_count += int(job_count)
        if observed is not None and job_count is not None:
            # De-seasonalize: divide by the row's quarter factor so a Q1 공채철
            # peak isn't mistaken for growth. seasonal_factor defaults to 1.0
            # (no baseline → no-op), keeping behaviour unchanged when absent.
            factor = _safe_factor(row.get("seasonal_factor"))
            value = float(job_count) / factor
            if observed > midpoint:
                recent.append(value)
            else:
                prior.append(value)
            if latest is None or observed > latest:
                latest = observed
        change_pct = row.get("change_pct")
        if change_pct is not None:
            changes.append(float(change_pct))

    recent_avg = sum(recent) / len(recent) if recent else None
    prior_avg = sum(prior) / len(prior) if prior else None
    momentum_pct = (
        (recent_avg - prior_avg) / prior_avg
        if recent_avg is not None and prior_avg not in (None, 0)
        else None
    )
    return HiringIndicators(
        observations=observations,
        recent_avg_job_count=recent_avg,
        prior_avg_job_count=prior_avg,
        momentum_pct=momentum_pct,
        avg_change_pct=(sum(changes) / len(changes)) if changes else None,
        total_job_count=total_job_count,
        prior_observations=len(prior),
        latest_observed_date=latest.isoformat() if latest else None,
        days_since_latest=(as_of - latest).days if latest else None,
        sector_demand_momentum=sector_momentum,
        sector_demand_coverage=sector_coverage,
        distinct_skills=len(skill_counter),
        skill_enriched_observations=skill_enriched_observations,
        top_skills=tuple(s for s, _ in skill_counter.most_common(5)),
    )


def _sector(sector_demand: dict | None) -> tuple[float | None, float]:
    if not sector_demand:
        return None, 0.0
    momentum = sector_demand.get("momentum_pct")
    coverage = float(sector_demand.get("coverage_weight") or 0.0)
    return (float(momentum) if momentum is not None else None), coverage


def _safe_factor(value) -> float:
    """Seasonal factor for de-seasonalization; falls back to 1.0 (no-op)."""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return 1.0
    return factor if factor > 0 else 1.0


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
