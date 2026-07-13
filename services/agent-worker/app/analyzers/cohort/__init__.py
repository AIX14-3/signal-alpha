"""LLM 코호트 채점 경로의 순수 로직 (evidence 압축·소스 스펙·SourceResult 매핑).

``scripts/cohort_llm_run.py`` (연구 러너) 에서 실측 검증된 로직의 프로덕션 승격.
오케스트레이션(큐 태스크)은 ``app/orchestrator/cohort/`` 에 있다.
"""

from app.analyzers.cohort.evidence import build_attention, build_evidence
from app.analyzers.cohort.mapping import to_source_result
from app.analyzers.cohort.sources import COHORT_SOURCES, CohortSourceSpec

__all__ = [
    "COHORT_SOURCES",
    "CohortSourceSpec",
    "build_attention",
    "build_evidence",
    "to_source_result",
]
