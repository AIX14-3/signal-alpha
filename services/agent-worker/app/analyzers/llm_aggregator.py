"""LLM 통합 판정기 — 6소스 점수·근거를 읽고 하나의 종합 판정을 만든다.

## 결정론 aggregator 와 무엇이 다른가
기존 ``orchestrator/aggregation/tasks.py::_aggregate`` 는 **등가중 산술평균**이었다
(``sum(w*score)/sum(w)``, weight_mode="equal"). 여기서는 LLM 이 근거를 읽고 판정한다:
소스 간 일치/충돌, 근거의 질, 표본 크기를 읽어 가중을 스스로 정한다.

## 결정론이 계속 소유하는 것 (LLM 이 못 건드림)
LLM 이 알 수 없거나, 정직성 장치이거나, FE 계약인 것들:
- ``no_signal``/``failed`` 소스 **제외** — 0으로 희석 금지. **프롬프트에 애초에 넣지 않는다.**
- ``consensus_score`` / ``source_agreement`` — 소스 간 방향 일치율(순수 산술)
- ``score_100 = (score+1)*50`` 경계 변환 (50 = 중립)
- ``confidence`` 상한 0.85 클램프
- 투자권유 필터

## contributing weight = 설명가능성 계약
기존 ``_meta.blend_basis`` 는 등가중 지분(``share``)을 실어 "HIRING 40%·PATENT 25% → +0.20"
식으로 헤드라인 근거를 렌더했다. 등가중이 사라지면 그 지분을 만들 주체가 없으므로, **LLM 이
자기 판단 가중을 직접 출력**하게 하고 그대로 ``blend_basis`` 로 흘린다
(``_meta.scoring_method = "llm_judgment"`` 로 정직 라벨링).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.analyzers.llm_scorer import (
    CONFIDENCE_CAP,
    SCHEMA_RETRIES,
    JsonLlm,
    LlmScorerError,
    StockScore,
    _ask,
)
from app.policy_safety import find_investment_advice_in

PROMPT_VERSION = "aggregate-v1"
_TEMPLATE = Path(__file__).resolve().parents[1] / "prompts" / "aggregate_v1.md"

_ALLOWED_SIGNALS = {"positive", "neutral", "negative", "mixed"}

# 통합 판정 출력 스키마 — responseSchema 로 **API 가 강제**한다.
# ⚠️ 스키마는 '형태'만 보장하고 '값의 의미'는 보장하지 않는다(Google 문서도 명시). score 범위
# [-1,1]·confidence 상한 0.85·no_signal 소스 기여 금지는 여전히 parse_verdicts 가 검증한다.
AGGREGATE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "verdicts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "ticker": {"type": "STRING"},
                    "final_score": {"type": "NUMBER"},
                    "confidence": {"type": "NUMBER"},
                    "signal": {
                        "type": "STRING",
                        "enum": ["positive", "neutral", "negative", "mixed"],
                    },
                    "conflict": {"type": "BOOLEAN"},
                    "headline": {"type": "STRING"},
                    "positive_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "caution_evidence": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "contributing": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "source": {"type": "STRING"},
                                "weight": {"type": "NUMBER"},
                                "why": {"type": "STRING"},
                            },
                            "required": ["source", "weight", "why"],
                        },
                    },
                },
                "required": [
                    "ticker", "final_score", "confidence", "signal", "conflict",
                    "headline", "positive_evidence", "caution_evidence", "contributing",
                ],
            },
        }
    },
    "required": ["verdicts"],
}


@dataclass(frozen=True)
class SourceContribution:
    source: str
    weight: float
    why: str


@dataclass(frozen=True)
class Verdict:
    ticker: str
    final_score: float
    confidence: float
    signal: str
    conflict: bool
    headline: str
    positive_evidence: list[str] = field(default_factory=list)
    caution_evidence: list[str] = field(default_factory=list)
    contributing: list[SourceContribution] = field(default_factory=list)
    # --- 결정론이 소유(LLM 이 만들지 않음) ---
    consensus_score: float = 50.0
    source_agreement: str = "LOW"

    @property
    def score_100(self) -> float:
        """발행 스케일. 50 = 중립 (경계 변환은 코드가 소유)."""
        return round((self.final_score + 1.0) * 50.0, 2)


def _agreement(scores: dict[str, StockScore]) -> tuple[float, str]:
    """방향 일치율 — LLM 이 알 수 없는 소스 간 메타. 결정론 유지."""
    dirs = [s.direction for s in scores.values() if not s.no_signal]
    if not dirs:
        return 50.0, "LOW"
    if len(dirs) == 1:
        return 50.0, "LOW"  # 단일 소스는 과신 방지로 50 고정(기존 동작)
    top = max(dirs.count(d) for d in set(dirs))
    rate = top / len(dirs)
    level = "HIGH" if rate >= 0.75 else "MEDIUM" if rate >= 0.5 else "LOW"
    return round(rate * 100, 2), level


def build_prompt(asof: str, per_stock: dict[str, dict[str, StockScore]], names: dict[str, str]) -> str:
    """``per_stock[ticker][source] = StockScore``.

    ⚠️ ``no_signal`` 소스는 **프롬프트에 넣지 않는다** — LLM 이 볼 수 없으면 희석시킬 수 없다.
    """
    cohort = []
    for ticker, by_source in per_stock.items():
        sources = {
            src: {
                "score": s.score,
                "confidence": s.confidence,
                "direction": s.direction,
                "evidence": s.evidence,
            }
            for src, s in by_source.items()
            if not s.no_signal
        }
        cohort.append({
            "ticker": ticker,
            "name": names.get(ticker, ""),
            "sources": sources,
            "sources_with_no_signal": [src for src, s in by_source.items() if s.no_signal],
        })
    payload = {"asof": asof, "cohort": cohort}
    template = _TEMPLATE.read_text(encoding="utf-8")
    return template.replace(
        "{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def parse_verdicts(
    payload: Any,
    per_stock: dict[str, dict[str, StockScore]],
) -> list[Verdict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        raise LlmScorerError("LLM aggregate response missing 'verdicts' list")
    by_ticker = {str(v.get("ticker")): v for v in payload["verdicts"] if isinstance(v, dict)}

    out: list[Verdict] = []
    for ticker, by_source in per_stock.items():
        item = by_ticker.get(ticker)
        if item is None:
            raise LlmScorerError(f"LLM aggregate missing cohort ticker: {ticker}")
        try:
            score = float(item.get("final_score", 0.0))
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise LlmScorerError(f"LLM aggregate non-numeric for {ticker}") from exc
        if not -1.0 <= score <= 1.0:
            raise LlmScorerError(f"LLM aggregate final_score out of range for {ticker}: {score}")

        signal = str(item.get("signal") or "neutral").lower()
        if signal not in _ALLOWED_SIGNALS:
            raise LlmScorerError(f"LLM aggregate bad signal for {ticker}: {signal}")

        positive = [str(x).strip() for x in (item.get("positive_evidence") or []) if str(x).strip()]
        caution = [str(x).strip() for x in (item.get("caution_evidence") or []) if str(x).strip()]
        headline = str(item.get("headline") or "").strip()

        hit = find_investment_advice_in([headline, *positive, *caution])
        if hit is not None:
            raise LlmScorerError(f"LLM aggregate contained investment advice: {hit}")

        scored_srcs = {s for s, v in by_source.items() if not v.no_signal}
        contributing = []
        for c in item.get("contributing") or []:
            if not isinstance(c, dict):
                continue
            src = str(c.get("source"))
            # no_signal 소스를 기여로 적으면 계약 위반 — 그 소스는 점수에 반영되면 안 된다.
            if src not in scored_srcs:
                raise LlmScorerError(
                    f"LLM aggregate credited a non-contributing source for {ticker}: {src}"
                )
            contributing.append(SourceContribution(
                source=src,
                weight=round(float(c.get("weight") or 0.0), 3),
                why=str(c.get("why") or "").strip(),
            ))

        consensus, agreement = _agreement(by_source)
        out.append(Verdict(
            ticker=ticker,
            final_score=round(score, 3),
            confidence=round(min(max(confidence, 0.0), CONFIDENCE_CAP), 3),
            signal=signal,
            conflict=bool(item.get("conflict", False)),
            headline=headline,
            positive_evidence=positive,
            caution_evidence=caution,
            contributing=contributing,
            consensus_score=consensus,
            source_agreement=agreement,
        ))
    return out


def blend_basis(verdict: Verdict) -> dict:
    """FE 계약 ``_meta.blend_basis`` — 등가중이 사라진 자리를 LLM 판단 가중이 채운다."""
    return {
        "sources": [
            {"source": c.source, "weight": c.weight, "share": c.weight, "why": c.why}
            for c in verdict.contributing
        ],
        "blend_score": verdict.final_score,
        "scoring_method": "llm_judgment",
    }


async def aggregate_cohort(
    client: JsonLlm,
    *,
    asof: str,
    per_stock: dict[str, dict[str, StockScore]],
    names: dict[str, str],
) -> list[Verdict]:
    """스키마는 ``AGGREGATE_SCHEMA`` 로 API 가 강제한다. 그래도 남는 의미 위반(점수 범위·
    no_signal 기여 등)은 사유를 되먹여 재시도(llm_scorer.score_cohort 와 동일 정책)."""
    if not per_stock:
        return []
    prompt = build_prompt(asof, per_stock, names)
    last: LlmScorerError | None = None
    for attempt in range(SCHEMA_RETRIES + 1):
        payload = await _ask(
            client,
            prompt if attempt == 0 else f"{prompt}\n\n## 직전 응답이 계약을 위반했다\n{last}\n"
            "반드시 위 JSON 스키마를 정확히 지켜 다시 응답하라.",
            AGGREGATE_SCHEMA,
        )
        try:
            return parse_verdicts(payload, per_stock)
        except LlmScorerError as exc:
            last = exc
    raise last or LlmScorerError("aggregate_cohort failed")
