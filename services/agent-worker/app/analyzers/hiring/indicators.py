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


@dataclass(frozen=True)
class DecayedHiringIndicators:
    """공고별 시간감쇠 집계(opt-in 경로). 채용을 이벤트로 보고 게시일 나이에 지수 반감기
    가중한 활동강도. 유효창을 넘거나 마감된 공고는 산입에서 빠진다(로드/DB 보존은 별개)."""

    active_count: int  # 유효창 내(비만료) 공고 수
    total_postings: int  # 로드된 전체 공고 수(참고용)
    decayed_activity: float  # Σ 0.5^(age/half_life)
    latest_posting_date: str | None
    days_since_latest: int | None
    distinct_skills: int = 0
    top_skills: tuple[str, ...] = ()
    # FE age 표시 재료: 활성 공고의 (게시일, 마감일) — 게시일 최신순. FE 가 렌더 시
    # (오늘 − 게시일)로 "N일 전"을 계산한다(절대날짜만 노출, age 숫자는 박제 금지).
    active_postings: tuple[tuple[str, str | None], ...] = ()
    # 감소 감지(옵션1)용: 유효창을 반으로 나눈 최근절반/직전절반 감쇠합. 직전절반은 반창
    # 경계 기준으로 재감쇠해 최근절반과 같은 스케일로 비교 가능하게 한다.
    recent_half_decayed: float = 0.0
    prior_half_decayed: float = 0.0
    # 감소 감지(옵션3=long_term_avg)용: 회사 장기 채용률 평균. 트레일링 장기창을 유효창
    # 크기 버킷으로 나눠 각 과거 버킷의 감쇠활동(버킷 자체 종단 기준 재감쇠 → 현재창과 동일
    # 스케일)을 구하고, 비어있지 않은 과거 버킷들의 평균을 낸다. long_term_window_days 를
    # 주지 않으면 0(=이 경로 비활성, prior_window 와 무관·회귀 보장).
    long_term_avg_decayed: float = 0.0
    long_term_baseline_buckets: int = 0  # 비어있지 않은 과거 버킷 수(소표본 가드 입력)


def compute_decayed_activity(
    postings: list[dict],
    *,
    as_of: date,
    window_days: int,
    half_life_days: float,
    long_term_window_days: int | None = None,
) -> DecayedHiringIndicators:
    """공고 리스트(각 posting_date·선택 closing_date_parsed·ocr_skills) → 감쇠활동 지표.

    - 유효창: ``age > window_days`` 또는 ``age < 0``(미래) → 제외.
    - 마감: ``closing_date_parsed < as_of`` → 제외.
    - 가중치: ``0.5 ** (age / half_life_days)`` — 반감기마다 절반(지수 감쇠).
    - ``long_term_window_days`` 를 주면(옵션3) 그 트레일링 창을 유효창 크기 버킷으로 나눠
      회사 장기 채용률 평균(``long_term_avg_decayed``)을 함께 낸다. 안 주면 그 필드는 0.
    """
    half_life = half_life_days if half_life_days and half_life_days > 0 else 1.0
    half_window = window_days / 2.0
    decayed = 0.0
    recent_half = 0.0
    prior_half = 0.0
    active = 0
    latest: date | None = None
    skill_counter: Counter[str] = Counter()
    active_rows: list[tuple[date, str | None]] = []
    for posting in postings:
        posted = _parse_date(posting.get("posting_date"))
        if posted is None:
            continue
        age = (as_of - posted).days
        if age < 0 or age > window_days:
            continue
        closing_raw = posting.get("closing_date_parsed")
        closing = _parse_date(closing_raw)
        if closing is not None and closing < as_of:
            continue
        active += 1
        decayed += 0.5 ** (age / half_life)
        if age <= half_window:
            recent_half += 0.5 ** (age / half_life)
        else:  # 직전절반: 반창 경계 기준 재감쇠(최근절반과 동일 스케일)
            prior_half += 0.5 ** ((age - half_window) / half_life)
        active_rows.append((posted, closing.isoformat() if closing else None))
        if latest is None or posted > latest:
            latest = posted
        for skill in posting.get("ocr_skills") or []:
            if skill:
                skill_counter[str(skill)] += 1
    active_rows.sort(key=lambda pc: pc[0], reverse=True)  # 게시일 최신순

    # 옵션3 baseline: 회사 장기 채용률 평균. 트레일링 long_term_window 를 유효창 크기 버킷으로
    # 나눠, 각 과거 버킷(k≥1)의 감쇠활동을 버킷 자체 종단 기준으로 재감쇠(현재창 k=0 과 동일
    # 스케일)해 합산하고, 비어있지 않은 과거 버킷의 평균을 낸다. 현재창(k=0)은 비교 대상이라
    # 제외. 마감 필터는 적용 안 함 — 지금 마감됐어도 그때는 실제 채용활동이었으므로(과거율).
    long_term_avg = 0.0
    baseline_buckets = 0
    if long_term_window_days and long_term_window_days > window_days:
        bucket_sums: dict[int, float] = {}
        for posting in postings:
            posted = _parse_date(posting.get("posting_date"))
            if posted is None:
                continue
            age = (as_of - posted).days
            if age < 0 or age > long_term_window_days:
                continue
            bucket = age // window_days
            if bucket == 0:
                continue  # 현재창 — baseline 에서 제외
            within = age - bucket * window_days  # 버킷 종단 기준 나이(현재창과 동일 스케일)
            bucket_sums[bucket] = bucket_sums.get(bucket, 0.0) + 0.5 ** (within / half_life)
        if bucket_sums:
            baseline_buckets = len(bucket_sums)
            long_term_avg = round(sum(bucket_sums.values()) / baseline_buckets, 4)

    return DecayedHiringIndicators(
        active_count=active,
        total_postings=len(postings),
        decayed_activity=round(decayed, 4),
        latest_posting_date=latest.isoformat() if latest else None,
        days_since_latest=(as_of - latest).days if latest else None,
        distinct_skills=len(skill_counter),
        top_skills=tuple(s for s, _ in skill_counter.most_common(5)),
        active_postings=tuple((d.isoformat(), c) for d, c in active_rows[:20]),
        recent_half_decayed=round(recent_half, 4),
        prior_half_decayed=round(prior_half, 4),
        long_term_avg_decayed=long_term_avg,
        long_term_baseline_buckets=baseline_buckets,
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
