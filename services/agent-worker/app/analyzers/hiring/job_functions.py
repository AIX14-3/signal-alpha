"""Pure job-function classification and sector-demand aggregation (C4).

Each ``hiring_raw_details`` row is one posting (``job_count`` 1) whose ``keyword``
is the job title. ``classify_job_function`` maps that title to a coarse function
(ENGINEER, SALES…) by Korean/English keyword rules — deterministic, LLM-free.

``aggregate_sector_demand`` turns peer companies' postings into a forward signal:
for the functions a target stock depends on, it measures whether sector-wide
demand for those functions is rising or falling (recent vs prior window), blended
by the stock's exposure weights. The target stock is excluded so this is purely a
peer/sector signal — the stock's own momentum is the analyzer's separate component.

No DB, no clock: the loader supplies rows + ``as_of`` and the weights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.analyzers.hiring.indicators import _parse_date  # shared date parser

# Ordered rules: first matching function wins. Keys are matched case-insensitively
# as substrings of the job title. Korean first (job titles are mostly Korean).
DEFAULT_FUNCTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ENGINEER", ("개발", "엔지니어", "engineer", "developer", "소프트웨어", "프로그래머",
                  "백엔드", "프론트", "데이터", "ai", "ml", "서버", "sw")),
    ("RESEARCH", ("연구", "r&d", "연구원", "선행", "박사", "research")),
    ("MANUFACTURING", ("생산", "제조", "공정", "품질", "설비", "양산", "manufactur", "공장")),
    ("SALES", ("영업", "세일즈", "sales", "판매", "리테일")),
    ("MARKETING", ("마케팅", "marketing", "브랜드", "홍보", "pr", "콘텐츠")),
    ("DESIGN", ("디자인", "design", "ux", "ui")),
    ("PLANNING", ("기획", "전략", "pm", "프로덕트", "사업개발", "planning")),
    ("CORPORATE", ("인사", "재무", "회계", "경영지원", "법무", "hr", "총무", "구매")),
)

LABELS: dict[str, str] = {
    "ENGINEER": "개발/엔지니어",
    "RESEARCH": "연구개발(R&D)",
    "MANUFACTURING": "생산/제조",
    "SALES": "영업",
    "MARKETING": "마케팅",
    "DESIGN": "디자인",
    "PLANNING": "기획/전략",
    "CORPORATE": "경영지원",
}


def classify_job_function(
    keyword: str | None,
    *,
    rules: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_FUNCTION_RULES,
) -> str | None:
    """Map a job title to a function key, or None when nothing matches."""
    if not keyword:
        return None
    text = keyword.casefold()
    for function_key, needles in rules:
        if any(needle in text for needle in needles):
            return function_key
    return None


@dataclass(frozen=True)
class FunctionDemand:
    function_key: str
    weight: float
    recent_count: float
    prior_count: float
    momentum_pct: float | None  # (recent - prior) / prior across peers; None if prior 0


@dataclass(frozen=True)
class SectorDemand:
    momentum_pct: float | None  # weight-weighted mean of per-function momentum
    coverage_weight: float      # summed weight of functions that had a computable momentum
    functions: list[FunctionDemand] = field(default_factory=list)

    def as_metadata(self) -> dict:
        return {
            "momentum_pct": self.momentum_pct,
            "coverage_weight": self.coverage_weight,
            "functions": [
                {
                    "function_key": f.function_key,
                    "weight": f.weight,
                    "momentum_pct": f.momentum_pct,
                    "recent_count": f.recent_count,
                    "prior_count": f.prior_count,
                }
                for f in self.functions
            ],
        }


def aggregate_sector_demand(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
    function_weights: dict[str, float],
    target_stock_id: int | None = None,
    classify=classify_job_function,
) -> SectorDemand:
    """Peer demand momentum for the functions a stock depends on.

    ``rows`` are cross-company postings (each with stock_id, keyword, job_count,
    observed_date). Rows from ``target_stock_id`` are excluded so the signal is
    purely peer/sector. Returns an all-None SectorDemand when there is no usable
    peer data, which makes the analyzer fall back to own-momentum only.
    """
    if not function_weights:
        return SectorDemand(momentum_pct=None, coverage_weight=0.0)

    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)
    recent: dict[str, float] = {}
    prior: dict[str, float] = {}
    for row in rows:
        if target_stock_id is not None and row.get("stock_id") == target_stock_id:
            continue
        function_key = classify(row.get("keyword"))
        if function_key is None or function_key not in function_weights:
            continue
        observed = _parse_date(row.get("observed_date"))
        if observed is None:
            continue
        count = float(row.get("job_count") or 0)
        bucket = recent if observed > midpoint else prior
        bucket[function_key] = bucket.get(function_key, 0.0) + count

    functions: list[FunctionDemand] = []
    weighted_sum = 0.0
    coverage = 0.0
    for function_key, weight in function_weights.items():
        recent_count = recent.get(function_key, 0.0)
        prior_count = prior.get(function_key, 0.0)
        momentum = (recent_count - prior_count) / prior_count if prior_count > 0 else None
        functions.append(
            FunctionDemand(
                function_key=function_key,
                weight=weight,
                recent_count=recent_count,
                prior_count=prior_count,
                momentum_pct=momentum,
            )
        )
        if momentum is not None and weight > 0:
            weighted_sum += momentum * weight
            coverage += weight

    momentum_pct = weighted_sum / coverage if coverage > 0 else None
    return SectorDemand(
        momentum_pct=momentum_pct,
        coverage_weight=coverage,
        functions=functions,
    )


