"""
HiringAnalyzer (Analyzer Protocol 준수)
========================================

채용공고 RawEvidence 목록을 분석해 SourceResult를 반환한다.

설계 원칙:
- DB 직접 쿼리 없음 — 기준선 데이터는 수집 시 extra_payload에 임베드됨
- Analyzer Protocol 준수 (app/analyzers/base.py)
- 순수 CPU-bound 연산 (3단계 Fallback 포함)

3단계 Fallback 기준선:
  Phase A (rolling_avg ≥ 1.0): 14일 이동평균 × 계절 가중치
  Phase B (rolling_avg < 1.0): DataLab 검색량 / 100 × 계절 가중치  (Cold Start)
  Phase C (no data):           1.0 (신뢰도 낮음, data_status="low_confidence")

metadata 필드 (base_collector.py가 extra_payload에 저장):
  rolling_avg_14d    : float | None  — 14일 이동평균
  avg_search_volume  : float | None  — 네이버 DataLab 검색 지수
  seasonal_factor    : float | None  — 현재 분기 계절 가중치 (q{N}_factor)

⚠️ 한국어 직무명 파싱 한계:
  공백 기준 분해 → "AI연구원" 미분해. KoNLPy 도입 전까지 영문 우선.
"""
from __future__ import annotations

import logging
from collections import Counter

from app.schemas.evidence import RawEvidence, SourceType
from app.schemas.source_result import Direction, EvidenceItem, SourceResult

logger = logging.getLogger(__name__)

# 방향성 판정 임계값
_MOMENTUM_STRONG = 30.0    # change_pct ≥ +30% → positive
_MOMENTUM_WEAK = -30.0     # change_pct ≤ -30% → negative

# Phase A 진입 최소 기준 (Cold Start 판정)
_MIN_ROLLING_AVG = 1.0

# Phase A 최소 기대값 — 실적 기반(14일 평균)일 때 분모 하한선
_MIN_EXPECTED_JOB = 1.0
# Phase B ZeroDivision 방어 — 네이버 검색량 기반 분모 폭발 방지 (0.01 = "거의 0이지만 0은 아님")
_ZERO_DIV_PROTECTION = 0.01

# 최소 공고 건수 미달 시 insufficient_data 반환 (0→소수건 오탐 방지)
_MIN_JOB_COUNT = 3

_JOB_TITLE_STOPWORDS = {
    "engineer", "researcher", "developer", "analyst",
    "개발", "연구", "엔지니어", "분석", "팀",
    "부", "팀장", "리드", "lead",
}


class HiringAnalyzer:
    source: SourceType = "HIRING"

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        if not evidence:
            return SourceResult(
                source="HIRING",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="채용 데이터 없음",
                data_status="failed",
            )

        job_count = len(evidence)

        # 최소 건수 방어 — 0→소수건 전환을 모멘텀으로 오해하는 현상 방지
        if job_count < _MIN_JOB_COUNT:
            return SourceResult(
                source="HIRING",
                stock_code=stock_code,
                direction="unknown",
                score=50.0,
                summary=f"채용 {job_count}건 (최소 기준 {_MIN_JOB_COUNT}건 미달)",
                data_status="insufficient_data",
            )

        # ── 기준선 선택 (3단계 Fallback) ────────────────────────────────────────
        # 모든 RawEvidence row는 동일 기업·날짜이므로 기준선 값이 복제됨 → max()로 대표값 추출
        rolling_avgs = [
            float(e.metadata["rolling_avg_14d"])
            for e in evidence
            if e.metadata.get("rolling_avg_14d") is not None
        ]
        rolling_avg = max(rolling_avgs) if rolling_avgs else 0.0

        search_vols = [
            float(e.metadata["avg_search_volume"])
            for e in evidence
            if e.metadata.get("avg_search_volume") is not None
        ]
        avg_search_volume = max(search_vols) if search_vols else None

        seasonal_factors = [
            float(e.metadata["seasonal_factor"])
            for e in evidence
            if e.metadata.get("seasonal_factor") is not None
        ]
        seasonal_factor = max(seasonal_factors) if seasonal_factors else 1.0

        if rolling_avg >= _MIN_ROLLING_AVG:
            # Phase A: 충분한 실적 데이터 (Day 14+)
            # _MIN_EXPECTED_JOB(1.0) 하한: rolling_avg가 0에 수렴해도 분모 안전
            expected = max(rolling_avg * seasonal_factor, _MIN_EXPECTED_JOB)
            phase = "A"
            data_status = "ok"
        elif avg_search_volume is not None and avg_search_volume > 0:
            # Phase B: Cold Start — DataLab 검색량 기반 fallback
            # 검색 지수(0~100) / 100 = 0.0~1.0 소수 스케일
            # max(..., _ZERO_DIV_PROTECTION=0.01): 네이버 검색량이 0에 가까워도
            # _MIN_EXPECTED_JOB(1.0)으로 올리면 Phase B와 C가 동일해지므로 미세 하한만 적용
            base_scale = avg_search_volume / 100.0
            expected = max(base_scale * seasonal_factor, _ZERO_DIV_PROTECTION)
            phase = "B"
            data_status = "ok"
        else:
            # Phase C: 데이터 전무 — 최소 기준값
            expected = _MIN_EXPECTED_JOB
            phase = "C"
            data_status = "low_confidence"

        change_pct = ((job_count - expected) / expected) * 100

        # ── 직무 분석 ────────────────────────────────────────────────────────────
        tech_counter: Counter[str] = Counter()
        job_titles: list[str] = []

        for e in evidence:
            meta = e.metadata
            tech_stack = meta.get("tech_stack", [])
            if isinstance(tech_stack, str):
                tech_stack = [t.strip() for t in tech_stack.split(",")]
            tech_counter.update(tech_stack)

            job_title = meta.get("job_title", e.title or "")
            if job_title:
                job_titles.append(job_title)

        top_techs = [t for t, _ in tech_counter.most_common(3)]
        top_keywords = _extract_title_keywords(job_titles, k=3)

        direction = _change_pct_to_direction(change_pct)
        score = _change_pct_to_score(change_pct)

        tech_str = ", ".join(top_techs) if top_techs else "없음"
        kw_str = ", ".join(top_keywords) if top_keywords else "없음"
        _phase_label = {"A": "14일 평균 대비", "B": "트렌드 기준 대비", "C": "기본 기준선 대비"}
        summary = (
            f"채용 {job_count}건 ({_phase_label[phase]} {change_pct:+.1f}%, Phase {phase}), "
            f"주요 기술: {tech_str}, 키워드: {kw_str}"
        )

        evidence_items = [
            EvidenceItem(
                title=e.title,
                summary=str(e.metadata.get("job_title", "")),
                url=e.url,
                published_at=e.published_at,
                source_name=e.metadata.get("source_type"),
            )
            for e in evidence
        ]

        logger.info(
            "HiringAnalyzer %s: job=%d expected=%.2f chg=%+.1f%% phase=%s dir=%s score=%.1f",
            stock_code, job_count, expected, change_pct, phase, direction, score,
        )

        return SourceResult(
            source="HIRING",
            stock_code=stock_code,
            direction=direction,
            score=score,
            summary=summary,
            evidence_items=evidence_items,
            data_status=data_status,
        )


# ──────────────────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────────────────

def _change_pct_to_direction(change_pct: float) -> Direction:
    if change_pct >= _MOMENTUM_STRONG:
        return "positive"
    elif change_pct <= _MOMENTUM_WEAK:
        return "negative"
    return "neutral"


def _change_pct_to_score(change_pct: float) -> float:
    """변화율 → 0~100 점수. 0% = 50점 기준, ±50%p 이상 = 상한/하한."""
    clamped = max(-50.0, min(50.0, change_pct))
    return round(50.0 + clamped, 1)


def _extract_title_keywords(job_titles: list[str], k: int = 3) -> list[str]:
    """직무명에서 불용어 제거 후 상위 k개 키워드 반환."""
    all_words: list[str] = []
    for title in job_titles:
        words = title.replace("/", " ").replace("-", " ").split()
        all_words.extend(
            w.strip() for w in words
            if w.strip().lower() not in _JOB_TITLE_STOPWORDS
        )
    return [w for w, _ in Counter(all_words).most_common(k)]
