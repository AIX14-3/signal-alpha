"""LLM 코호트 채점기 — 정규화된 증거를 LLM 이 직접 읽고 점수를 낸다.

## 결정론 수식과 무엇이 다른가
기존 ``analyzers/scoring.graded`` 는 사람이 정한 상수(scale/weight)로 점수를 계산했다. 여기서는
**수식도, 채점표도 주지 않는다.** LLM 이 정규화된 증거를 직접 읽고 판단한다.

## 그럼 '기준'은 어디서 오는가 — 채점표가 아니라 **비교 컨텍스트**
1. **자기 과거**(``self_history``): 같은 회사의 평소 수준 → 회사별 base rate 차이를 흡수.
2. **섹터 코호트**(``cohort``): 여러 종목을 **한 프롬프트에 함께** 넣어 **상대 채점**시킨다.

코호트 배치 채점이 일관성의 주 방어수단이다. LLM 은 절대 채점보다 상대 순위가 훨씬 안정적이고,
호출 수도 종목당 1회 → 코호트당 1회로 줄어 비용이 코호트 크기만큼 내려간다.

⚠️ **강제 분산 금지**: "코호트 전원 중립"이 허용돼야 한다. 순위 정규화를 강제하면 조용한 날에도
인공 신호가 만들어진다(프롬프트에 규범으로 명시).

## DATALAB 축 분리 — 이 모듈의 가장 중요한 안전장치
검색 급증(attention)은 이 레포에서 **유일하게 실증된 신호**다. 단 그것은 **매그니튜드**
(→ 미래 거래량·변동성, IC +0.316) 축이지 **방향**이 아니다(방향 IC ≈ 0, BH-FDR 생존 0).

LLM 이 점수를 소유하면 "검색이 급증했으니 positive" 로 오역해 이 신호를 파괴할 위험이 크다.
가장 강한 보호는 프롬프트가 아니라 **스키마**다:

- ``attention_*`` 는 **코드가 계산**하고(``analyzers/datalab/attention.py`` 의 rolling-z 는 판단이
  아니라 산술이다) LLM 에는 **읽기 전용 컨텍스트**로만 준다.
- **LLM 출력 스키마에 attention 필드는 존재하지 않는다.** 쓸 수단이 없으면 오염시킬 수 없다.
- 프롬프트에도 규범으로 못박는다("급증 자체는 방향 근거가 아니다").

## 코드는 세고, LLM 은 판단한다
``indicators.py`` 의 **판단성 파생 비율**(momentum_pct·spike_ratio·net_polarity)은 주지 않는다 —
그건 사람이 정한 기준이고, 그걸 주면 LLM 은 남의 판단을 베끼게 된다. 대신 **시계열·목록 그
자체**를 주고 형태를 스스로 읽게 한다. 산술·집계(버킷·rolling-z·카운트)는 코드가 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.policy_safety import find_investment_advice_in

PROMPT_VERSION = "source-score-v1"
_TEMPLATE = Path(__file__).resolve().parents[1] / "prompts" / "source_score_v1.md"

CONFIDENCE_CAP = 0.85
"""confidence 상한. 기획 §10 'confidence 회피'(과신 방지)를 상한으로 보존한다 — LLM 은 자기
확신을 과대평가하는 경향이 강해 '100% 확신'을 남발한다."""


class LlmScorerError(RuntimeError):
    """LLM 채점 실패 — 호출측은 결정론 폴백 또는 no_signal 로 강등한다."""


class JsonLlm(Protocol):
    """``GeminiJsonClient`` / 향후 다른 프로바이더가 만족하는 최소 계약."""

    @property
    def model(self) -> str: ...

    async def generate_json(self, prompt: str) -> Any: ...


# 소스별 가이드 — "무엇이 확장/위축의 흔적인가"를 말해줄 뿐, **수치 임계표는 주지 않는다.**
SOURCE_GUIDES: dict[str, str] = {
    "HIRING": """## 이 소스: 채용 공고 (HIRING)

`postings` 는 관측된 채용 공고, `monthly_counts` 는 최근 12개월 월별 공고 수다.

- **확장의 흔적(+)**: 공고 수가 자기 평소 대비 늘어남, 직무 폭이 넓어짐, 신규 기술·신사업 직무 등장.
- **위축의 흔적(−)**: 공고 수가 평소 대비 뚜렷이 줄어듦, 채용이 사실상 멈춤.
- 대기업 공채는 **계절성**이 크다(상·하반기 몰림). 자기 과거의 같은 시기와 비교하라 —
  단순히 전월 대비 늘었다고 확장이 아니다.
- 표본이 매우 적으면(공고 몇 건) 판단하지 말고 `no_signal`.""",
    "PATENT": """## 이 소스: 특허 (PATENT)

`publications` 는 최근 공개된 특허, `yearly_counts` 는 연도별 출원 건수다.

- **확장의 흔적(+)**: 공개 건수가 자기 평소 대비 늘어남, **새로운 기술 분류**의 등장(신사업 진입).
- **위축의 흔적(−)**: R&D 산출이 뚜렷이 줄어듦.
- ⚠️ **특허는 매우 느린 신호다.** 출원 후 공개까지 ~18개월 걸린다. 최근 공개가 없다고 해서
  회사가 R&D 를 멈춘 게 아니다. 오래된 데이터뿐이면 `no_signal` 로 두는 편이 정직하다.
- 절대 건수는 회사 규모에 비례한다(삼성전자는 원래 수만 건). **반드시 자기 과거와 비교**하라.""",
    "DATALAB": """## 이 소스: 네이버 검색 트렌드 (DATALAB)

`search_series` 는 일별 검색 지수, `attention` 은 **코드가 계산한** 급증 지표다.

### ⛔ 가장 중요한 규칙 — 검색량 급증은 **방향 근거가 아니다**
검색 급증(`attention.tier` = 주의/주목/급증)이 확실히 예측하는 것은 **"앞으로 거래량과 변동성이
커진다"** 뿐이다(실증됨). **어느 쪽으로 움직일지는 알려주지 않는다.**

호재에도 악재에도 검색은 똑같이 폭증한다(리콜·횡령·실적쇼크에도 검색은 치솟는다). 따라서:

- **`attention` 을 이유로 `score` 를 움직이지 마라.** "검색이 급증했으니 positive" 는 **틀렸다.**
- `attention` 은 참고용 배경 정보로만 읽어라. 네 출력에는 attention 필드가 아예 없다.

### 그럼 DATALAB 의 극성은 어디서 오나
오직 **검색어 구성(composition)의 변화**에서만 온다:
- **확장의 흔적(+)**: 제품·서비스·채용 등 **수요(demand)** 키워드의 비중이 늘어남.
- **위축의 흔적(−)**: 리콜·불매·사고·소송 등 **리스크(risk)** 키워드의 비중이 늘어남.
- 총 검색량 수준이나 급증 자체는 극성의 근거가 **될 수 없다**.
- 구성 변화가 뚜렷하지 않으면 `no_signal`.""",
    "DART": """## 이 소스: 공시 (DART)

`events` 는 공시·지분 이벤트다.

- **확장/개선의 흔적(+)**: 자기주식 취득, 내부자·최대주주 지분 확대, 대형 공급계약.
- **위축/악화의 흔적(−)**: 유상증자, 전환사채(CB)·신주인수권부사채(BW) 발행, 내부자 지분 축소.
- 공시는 **확정된 사실**이므로 다른 소스보다 근거가 단단하다. 다만 **상승장에서는 내부자 매수가
  기본값**처럼 나타난다는 점을 감안하라 — 순매수 자체를 과대해석하지 마라.
- 방향성 있는 이벤트가 하나도 없으면 `no_signal`.""",
    "REPORT": """## 이 소스: 증권사 리포트 (REPORT)

`targets` 는 목표주가 이력, `current_price` 는 현재가다.

- **개선의 흔적(+)**: 목표주가가 **직전 대비 상향** 조정됨.
- **악화의 흔적(−)**: 목표주가가 **하향** 조정됨.
- ⚠️ **투자의견(매수/중립)은 근거로 쓰지 마라.** 애널리스트 의견은 구조적으로 매수 편향이 있어
  거의 항상 '매수'다 — 정보가 없다. **목표주가의 변화(revision)** 만 신호다.
- 목표가와 현재가의 괴리(upside)도 매수 편향이 남아 있으니 약하게만 반영하라.
- 목표주가 이력이 1건뿐이면 변화를 알 수 없다 → `no_signal`.""",
    "PRICE": """## 이 소스: 주가·수급 (PRICE)

`ohlcv` 는 일별 종가·거래량, `flows` 는 외국인·기관 순매수다.

- ⚠️ **이 소스는 다른 소스와 성격이 다르다.** 과거 가격으로 미래 가격을 말하는 것은 가장
  차익거래가 많이 된 영역이라 정보가 거의 없다. **강한 확신을 갖지 마라.**
- **확장/개선의 흔적(+)**: 외국인·기관의 **지속적** 순매수, 안정적 상승 추세.
- **위축/악화의 흔적(−)**: 외국인·기관의 지속적 순매도, 추세 붕괴.
- 하루 이틀의 등락은 노이즈다. **추세와 수급의 지속성**만 보라.
- 애매하면 `no_signal`. 여기서 억지로 점수를 내는 것은 해롭다.""",
}


@dataclass(frozen=True)
class StockContext:
    """코호트 안의 한 종목. ``evidence`` 는 정규화된 증거(파생 비율 아님)."""

    ticker: str
    name: str
    evidence: dict[str, Any]
    self_history: dict[str, Any] = field(default_factory=dict)
    # DATALAB 전용: 코드가 계산한 매그니튜드 축. **읽기 전용** — LLM 출력 스키마엔 없다.
    attention: dict[str, Any] | None = None
    prev_score: float | None = None


@dataclass(frozen=True)
class StockScore:
    ticker: str
    score: float
    confidence: float
    no_signal: bool
    evidence: list[str]
    score_change_reason: str | None = None

    @property
    def direction(self) -> str:
        if self.no_signal:
            return "neutral"
        if self.score >= 0.2:
            return "positive"
        if self.score <= -0.2:
            return "negative"
        return "neutral"


def build_prompt(source: str, asof: str, cohort: list[StockContext]) -> str:
    guide = SOURCE_GUIDES.get(source)
    if guide is None:
        raise LlmScorerError(f"unknown source for scoring: {source}")
    payload = {
        "source": source,
        "asof": asof,
        "cohort": [
            {
                "ticker": stock.ticker,
                "name": stock.name,
                "evidence": stock.evidence,
                "self_history": stock.self_history,
                **({"attention": stock.attention} if stock.attention else {}),
                **({"prev_score": stock.prev_score} if stock.prev_score is not None else {}),
            }
            for stock in cohort
        ],
    }
    template = _TEMPLATE.read_text(encoding="utf-8")
    return template.replace("{{SOURCE_GUIDE}}", guide).replace(
        "{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def parse_scores(payload: Any, cohort: list[StockContext]) -> list[StockScore]:
    """LLM 응답 → ``StockScore``. 계약 위반이면 ``LlmScorerError`` (호출측이 폴백)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("scores"), list):
        raise LlmScorerError("LLM response missing 'scores' list")
    by_ticker = {str(item.get("ticker")): item for item in payload["scores"] if isinstance(item, dict)}

    out: list[StockScore] = []
    for stock in cohort:
        item = by_ticker.get(stock.ticker)
        if item is None:
            raise LlmScorerError(f"LLM response missing cohort ticker: {stock.ticker}")

        no_signal = bool(item.get("no_signal", False))
        try:
            score = 0.0 if no_signal else float(item.get("score", 0.0))
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise LlmScorerError(f"LLM returned non-numeric score/confidence for {stock.ticker}") from exc
        if not -1.0 <= score <= 1.0:
            raise LlmScorerError(f"LLM score out of range for {stock.ticker}: {score}")

        evidence = [str(e).strip() for e in (item.get("evidence") or []) if str(e).strip()]
        reason = item.get("score_change_reason") or None

        # 투자권유 표현은 근거 문장으로도 나가면 안 된다(발행물에 그대로 실린다).
        hit = find_investment_advice_in([*evidence, str(reason or "")])
        if hit is not None:
            raise LlmScorerError(f"LLM score evidence contained investment advice: {hit}")

        out.append(
            StockScore(
                ticker=stock.ticker,
                score=round(score, 3),
                # 상한 클램프 — 프롬프트가 아니라 **코드**가 강제한다.
                confidence=round(min(max(confidence, 0.0), CONFIDENCE_CAP), 3),
                no_signal=no_signal,
                evidence=evidence,
                score_change_reason=str(reason) if reason else None,
            )
        )
    return out


async def score_cohort(
    client: JsonLlm,
    *,
    source: str,
    asof: str,
    cohort: list[StockContext],
) -> list[StockScore]:
    """코호트 전체를 **한 번의 호출**로 상대 채점한다."""
    if not cohort:
        return []
    prompt = build_prompt(source, asof, cohort)
    payload = await client.generate_json(prompt)
    return parse_scores(payload, cohort)
