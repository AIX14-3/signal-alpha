"""``StockScore``(LLM 코호트 채점 출력) → ``SourceResult``(기존 발행 계약) 매핑.

스케일 규약: ``StockScore.score`` 와 ``SourceResult.score`` 는 둘 다 부호 있는
[-1, +1] — **여기서 변환하지 않는다.** 0-100 변환은 persistence 의 write 경계
(``alternative_persistence._to_100``) 한 곳에서만 일어난다.

provenance(``analysis_source="llm"`` + ``prompt_ver`` + ``llm_model``)가 "LLM 이
실제로 발행됐다"의 관측 지표다 — 검증/모니터링이 이 세 필드를 본다.
"""

from __future__ import annotations

from app.analyzers.llm_scorer import PROMPT_VERSION, StockScore
from app.schemas.source_result import EvidenceItem, SourceResult

_MAX_EVIDENCE_ITEMS = 5


def to_source_result(
    score: StockScore,
    *,
    source: str,
    stock_code: str,
    llm_model: str | None,
) -> SourceResult:
    """LLM 채점 1건 → 기존 persistence 가 소비하는 ``SourceResult``.

    - ``no_signal`` → ``data_status="no_signal"`` (score 는 parse_scores 가 이미 0 강제;
      집계 fan-in 이 no_signal 을 제외해 0점 희석이 없다 — 기존 계약과 일치)
    - LLM confidence[0,0.85] 는 ``SourceResult`` 의 결정론 confidence 와 **다른 축**이라
      전용 필드(``llm_confidence``)로 분리해 method_detail 로만 흘린다.
    """
    first = score.evidence[0] if score.evidence else ""
    if score.no_signal:
        summary = f"{source} LLM 판단: 신호 없음(기권)" + (f" — {first}" if first else "")
    else:
        summary = f"{source} LLM 채점 {score.score:+.2f}" + (f" — {first}" if first else "")

    return SourceResult(
        source=source,  # type: ignore[arg-type]  # 호출측이 SourceType 만 넘긴다
        stock_code=stock_code,
        direction=score.direction,  # type: ignore[arg-type]  # positive/neutral/negative
        score=score.score,
        summary=summary,
        evidence_items=[
            EvidenceItem(title=e[:120], summary=e) for e in score.evidence[:_MAX_EVIDENCE_ITEMS]
        ],
        data_status="no_signal" if score.no_signal else "ok",
        analysis_source="llm",
        prompt_ver=PROMPT_VERSION,
        llm_model=llm_model,
        llm_confidence=score.confidence,
        score_change_reason=score.score_change_reason,
    )
