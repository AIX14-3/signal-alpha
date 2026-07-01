"""LLM significance classifier for the Patent tier-B agent (기획 §2.1).

Given the deterministic rule signal plus the actual filings in the window, judge
how *strategically material* the patent activity is and write a one-sentence
Korean rationale. The rules already produced a prelabel from the filing pattern —
the LLM's job is to confirm (or correct) it and explain *why*, NOT to invent a
buy/sell call (docs §9/§10). Output is a strict JSON contract validated here; on a
malformed or out-of-enum response the verdict degrades to ``materiality=None`` so
the agent falls back to the deterministic prelabel.

Boundary: this is the agent/enrichment layer — it may call an LLM. The rule
analyzers never import it (analyzers stay deterministic and only read the cached
per-patent features an offline enrichment tool produced).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# The window-level materiality tag (distinct from the per-patent ``significance``
# scalar the offline enricher caches). A trace tag only — never a buy/sell call.
Materiality = Literal["strategic", "sustained", "routine", "ambiguous"]
MATERIALITY_VALUES: set[str] = {"strategic", "sustained", "routine", "ambiguous"}
PROMPT_VERSION = "patent-significance-v1"

_MAX_FILINGS_IN_PROMPT = 8
_MAX_RATIONALE_CHARS = 300


@dataclass(frozen=True)
class MaterialityVerdict:
    materiality: Materiality | None
    rationale: str
    confidence: float


class PatentSignificanceClassifier:
    """Wrap a JSON LLM client to classify a stock's window-level patent materiality."""

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
        summary: str,
        filings: list[dict[str, Any]],
        prelabel: Materiality,
    ) -> MaterialityVerdict:
        """Return the LLM's materiality verdict. Raises on transport/JSON failure so
        the caller's fallback path engages (deterministic prelabel)."""
        payload = await self._client.generate_json(
            _build_prompt(
                stock_code=stock_code,
                rule_direction=rule_direction,
                rule_score=rule_score,
                summary=summary,
                filings=filings,
                prelabel=prelabel,
            )
        )
        return _parse_verdict(payload, fallback=prelabel)


def _build_prompt(
    *,
    stock_code: str,
    rule_direction: str,
    rule_score: float,
    summary: str,
    filings: list[dict[str, Any]],
    prelabel: Materiality,
) -> str:
    return (
        "너는 한 기업의 최근 특허 출원 '묶음'이 그 회사 사업에 얼마나 전략적으로 중요한지\n"
        "판정하는 분석 보조다. 매수/매도 추천이나 수익 전망은 절대 하지 말고, 아래 규칙 신호와\n"
        "실제 출원 내역만 근거로 활동의 '유형'을 하나로 판정하라.\n\n"
        "유형 정의:\n"
        "- strategic : 신규 기술분류/핵심 플랫폼 특허가 눈에 띄어 사업 방향 전환·확장을 시사.\n"
        "- sustained : 핵심 사업 영역에서 꾸준·활발한 R&D가 이어짐(방향 전환 아님).\n"
        "- routine   : 방어적/소규모/정체된 출원으로 특별한 신호가 약함.\n"
        "- ambiguous : 위 셋 중 하나로 분명히 판정하기 어렵다.\n\n"
        f"종목코드: {stock_code}\n"
        f"규칙 분석 방향/점수: {rule_direction} / {rule_score:+.3f}\n"
        f"규칙 요약: {summary}\n"
        f"규칙 예비 판정(출원 패턴 기반): {prelabel}\n"
        f"최근 주요 출원(최대 {_MAX_FILINGS_IN_PROMPT}건):\n"
        f"{_format_filings(filings)}\n\n"
        "예비 판정을 확인하거나, 실제 출원 내역이 다른 유형을 강하게 시사하면 정정하라.\n"
        "다음 JSON만 출력하라(다른 텍스트 금지):\n"
        '{"materiality": "strategic|sustained|routine|ambiguous", '
        '"rationale": "한국어 한 문장 근거(매수/매도 표현 금지)", '
        '"confidence": 0.0~1.0 사이 숫자}'
    )


def _format_filings(filings: list[dict[str, Any]]) -> str:
    if not filings:
        return "  (표시할 출원 없음)"
    lines: list[str] = []
    for row in filings[:_MAX_FILINGS_IN_PROMPT]:
        title = str(row.get("patent_title") or "(제목 없음)").strip()
        tech = str(row.get("tech_category") or "-").strip()
        new_flag = " [신규분류]" if row.get("is_new_category") else ""
        significance = row.get("significance")
        sig = f" 중요도{float(significance):.2f}" if significance is not None else ""
        lines.append(f"  - {title} (분류 {tech}{new_flag}){sig}")
    return "\n".join(lines)


def _parse_verdict(payload: Any, *, fallback: Materiality) -> MaterialityVerdict:
    if not isinstance(payload, dict):
        return MaterialityVerdict(
            materiality=fallback,
            rationale="(LLM 응답 형식 오류 — 규칙 예비 판정 사용)",
            confidence=0.0,
        )
    raw = str(payload.get("materiality") or "").strip().lower()
    materiality: Materiality | None = raw if raw in MATERIALITY_VALUES else None  # type: ignore[assignment]
    rationale = str(payload.get("rationale") or "").strip()[:_MAX_RATIONALE_CHARS] or "(근거 미제공)"
    confidence = _clamp_float(payload.get("confidence"))
    return MaterialityVerdict(materiality=materiality, rationale=rationale, confidence=confidence)


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))
