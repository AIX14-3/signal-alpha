"""LLM cause classifier for the DataLab attention agent (협업안 §4).

Confirms the deterministic lead/lag prelabel and writes a human rationale: given
the search-vs-price timing features, is the spike a *catalyst*, *fomo*, or
*price_led*? The rules already produced a prelabel — the LLM's job is to confirm
(or correct) it and explain *why* in one sentence, NOT to invent a buy/sell call
(docs §9/§10). Output is a strict JSON contract validated here; on a malformed or
out-of-enum response the verdict degrades to ``cause=None`` so the agent can fall
back to the deterministic prelabel.

Boundary: this is the agent/enrichment layer — it may call an LLM. The rule
analyzers never import it (analyzers stay deterministic).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.datalab.lead_lag import LeadLag
from app.agents.requery_focus import requery_focus_prompt_block
from app.schemas.source_result import Cause

_CAUSE_VALUES: set[str] = {"catalyst", "fomo", "price_led"}
PROMPT_VERSION = "datalab-cause-v1"


@dataclass(frozen=True)
class CauseVerdict:
    cause: Cause | None
    rationale: str
    confidence: float


class DataLabCauseClassifier:
    """Wrap a JSON LLM client to classify a DataLab spike's cause."""

    def __init__(self, client: Any, *, prompt_version: str = PROMPT_VERSION) -> None:
        self._client = client
        self.prompt_version = prompt_version

    @property
    def model(self) -> str:
        return getattr(self._client, "model", None) or "gemini"

    async def classify(
        self,
        *,
        stock_code: str,
        rule_direction: str,
        rule_score: float,
        lead_lag: LeadLag,
        summary: str,
        requery_focus: str | None = None,
    ) -> CauseVerdict:
        """Return the LLM's cause verdict. Raises on transport/JSON failure so the
        caller's fallback path engages (deterministic prelabel).

        ``requery_focus`` (optional): the orchestrator's re-query hint — when set,
        a focused re-read instruction is appended to the prompt; ``None`` leaves
        the prompt byte-identical to a normal analyze."""
        payload = await self._client.generate_json(
            _build_prompt(
                stock_code=stock_code,
                rule_direction=rule_direction,
                rule_score=rule_score,
                lead_lag=lead_lag,
                summary=summary,
                requery_focus=requery_focus,
            )
        )
        return _parse_verdict(payload, fallback=lead_lag.preliminary_cause)


def _build_prompt(
    *,
    stock_code: str,
    rule_direction: str,
    rule_score: float,
    lead_lag: LeadLag,
    summary: str,
    requery_focus: str | None = None,
) -> str:
    def _pct(value: float | None) -> str:
        return "데이터 없음" if value is None else f"{value * 100:+.1f}%"

    return (
        "너는 한국 주식의 네이버 검색 트렌드 급증의 '원인 유형'을 분류하는 분석 보조다.\n"
        "매수/매도 추천이나 수익 전망은 절대 하지 말고, 검색과 주가의 '시점 관계'만 근거로\n"
        "급증이 어떤 유형인지 한 가지로 판정하라.\n\n"
        "원인 유형 정의:\n"
        "- catalyst : 검색이 주가보다 먼저 올랐다(정보가 가격에 선행).\n"
        "- fomo     : 주가가 먼저 오른 뒤 검색이 뒤따랐다(군중 추종).\n"
        "- price_led: 검색 변화 없이 주가만 움직였고 검색은 그저 추종/후행했다.\n"
        "- ambiguous: 위 셋 중 어느 것으로도 분명히 판정하기 어렵다.\n\n"
        f"종목코드: {stock_code}\n"
        f"규칙 분석 방향/점수: {rule_direction} / {rule_score:+.3f}\n"
        f"규칙 요약: {summary}\n"
        f"검색 모멘텀(최근 vs 이전): {_pct(lead_lag.search_momentum_pct)}\n"
        f"주가 이전구간 수익률: {_pct(lead_lag.price_prior_return)}\n"
        f"주가 최근구간 수익률: {_pct(lead_lag.price_recent_return)}\n"
        f"규칙 예비 판정(타이밍): {lead_lag.preliminary_cause or 'ambiguous'} — {lead_lag.note}\n\n"
        f"{requery_focus_prompt_block(requery_focus)}"
        "다음 JSON만 출력하라(다른 텍스트 금지):\n"
        '{"cause": "catalyst|fomo|price_led|ambiguous", '
        '"rationale": "한국어 한 문장 근거(매수/매도 표현 금지)", '
        '"confidence": 0.0~1.0 사이 숫자}'
    )


def _parse_verdict(payload: Any, *, fallback: Cause | None) -> CauseVerdict:
    if not isinstance(payload, dict):
        return CauseVerdict(cause=fallback, rationale="(LLM 응답 형식 오류 — 규칙 예비 판정 사용)", confidence=0.0)
    raw_cause = str(payload.get("cause") or "").strip().lower()
    cause: Cause | None = raw_cause if raw_cause in _CAUSE_VALUES else None  # type: ignore[assignment]
    rationale = str(payload.get("rationale") or "").strip() or "(근거 미제공)"
    confidence = _clamp_float(payload.get("confidence"))
    return CauseVerdict(cause=cause, rationale=rationale, confidence=confidence)


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
