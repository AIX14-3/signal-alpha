"""최종 리스크 리포트(JSON) 계약 — architecture.mermaid의 "결과물 리스크 리포트 (JSON)".

끝단 LLM 종합 단계의 산출물. **점수·방향·발행여부 등 수치/판정은 결정론·ML·게이트가 정한
값을 그대로** 담고(LLM이 못 바꿈), LLM은 ``headline``/``narrative``/``key_points``/
``caution_points`` 같은 **설명 필드만** 채운다. LLM 미사용/실패 시 결정론 요약으로 폴백한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RiskReport:
    stock_id: int
    stock_code: str
    signal_date: str | None
    # --- 결정론/ML/게이트 산출(불변) ---
    signal: str
    final_score: float | None
    confidence: float | None
    warning_level: str
    is_published: bool
    needs_review: bool
    vetoed: bool
    veto_keywords: list[str] = field(default_factory=list)
    ml_risk: dict[str, Any] | None = None  # {combined_vol, confidence, method} or None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    # --- LLM 설명 필드(또는 결정론 폴백) ---
    headline: str = ""
    narrative: str = ""
    key_points: list[str] = field(default_factory=list)
    caution_points: list[str] = field(default_factory=list)
    narrative_source: str = "deterministic"  # 'llm' | 'deterministic' | 'llm_fallback'

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock_id": self.stock_id,
            "stock_code": self.stock_code,
            "signal_date": self.signal_date,
            "signal": self.signal,
            "final_score": self.final_score,
            "confidence": self.confidence,
            "warning_level": self.warning_level,
            "is_published": self.is_published,
            "needs_review": self.needs_review,
            "vetoed": self.vetoed,
            "veto_keywords": list(self.veto_keywords),
            "ml_risk": self.ml_risk,
            "evidence": self.evidence,
            "narrative": {
                "headline": self.headline,
                "body": self.narrative,
                "key_points": list(self.key_points),
                "caution_points": list(self.caution_points),
                "source": self.narrative_source,
            },
        }
