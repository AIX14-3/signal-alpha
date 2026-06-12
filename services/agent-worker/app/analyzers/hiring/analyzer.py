"""
HiringAnalyzer
==============

채용공고 RawEvidence 목록을 분석해 SourceResult를 반환한다.

설계 원칙:
- DB 직접 쿼리 없음 — previous_job_count / change_pct 는 수집 시 hiring_raw_details에 저장됨
- Analyzer Protocol 준수 (app/analyzers/base.py)
- 순수 CPU-bound 연산만 수행하므로 async 선언만으로 충분

⚠️ 한국어 직무명 파싱 한계:
  공백 기준 분해 → "AI연구원" 미분해. KoNLPy 도입 전까지 영문 우선.
"""
from __future__ import annotations

import json
import logging
from collections import Counter

from app.schemas.evidence import RawEvidence, SourceType
from app.schemas.source_result import Direction, EvidenceItem, SourceResult

logger = logging.getLogger(__name__)

_MOMENTUM_STRONG = 30.0
_MOMENTUM_MODERATE = 10.0

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

        # previous_job_count는 종목 단위 전일 총계이므로 모든 row에 동일한 값이 복제됨.
        # 누락 row가 섞여 평균을 오염시키지 않도록 None 제외 후 최댓값 하나를 대표값으로 사용.
        prev_counts = [
            e.metadata["previous_job_count"]
            for e in evidence
            if e.metadata.get("previous_job_count") is not None
        ]
        avg_prev = max(prev_counts) if prev_counts else 1
        # avg_prev=0(어제 공고 0건)일 때 ZeroDivisionError 방지
        change_pct = ((job_count - avg_prev) / max(avg_prev, 1)) * 100

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
        summary = (
            f"채용 {job_count}건 (전일 대비 {change_pct:+.1f}%), "
            f"주요 기술: {tech_str}"
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
            "HiringAnalyzer %s: job=%d prev=%.1f chg=%+.1f%% dir=%s score=%.1f",
            stock_code, job_count, avg_prev, change_pct, direction, score,
        )

        return SourceResult(
            source="HIRING",
            stock_code=stock_code,
            direction=direction,
            score=score,
            summary=summary,
            evidence_items=evidence_items,
            data_status="ok",
        )


# ──────────────────────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────────────────────

def _change_pct_to_direction(change_pct: float) -> Direction:
    if change_pct >= _MOMENTUM_MODERATE:
        return "positive"
    elif change_pct <= -_MOMENTUM_STRONG:
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
