"""LLM 코호트 채점기 — 계약 고정.

핵심 불변식 3개:
  1. DATALAB 의 attention(매그니튜드)은 **LLM 출력 스키마에 존재하지 않는다.** 쓸 수단이
     없으면 오염시킬 수 없다 — 이 레포에서 유일하게 실증된 신호를 방향으로 오역하는 걸 막는
     가장 강한 보호막이다(프롬프트가 아니라 스키마로 막는다).
  2. confidence 상한 0.85 는 **코드**가 클램프한다(프롬프트 준수에 의존하지 않는다).
  3. 근거 문장에 투자권유가 섞이면 거부한다(근거는 발행물에 그대로 실린다).
"""

from __future__ import annotations

import asyncio

import pytest

from app.analyzers.llm_scorer import (
    CONFIDENCE_CAP,
    LlmScorerError,
    StockContext,
    build_prompt,
    parse_scores,
    score_cohort,
)

COHORT = [
    StockContext(ticker="005930", name="삼성전자", evidence={"rows": 3}),
    StockContext(ticker="000660", name="SK하이닉스", evidence={"rows": 5}),
]


def _payload(**overrides):
    base = {
        "scores": [
            {"ticker": "005930", "score": 0.4, "confidence": 0.6, "no_signal": False,
             "evidence": ["채용 공고가 전월 대비 30% 증가"]},
            {"ticker": "000660", "score": -0.3, "confidence": 0.5, "no_signal": False,
             "evidence": ["특허 공개 건수가 평소의 절반"]},
        ]
    }
    base["scores"][0].update(overrides)
    return base


# --- 1. DATALAB 축 분리 ------------------------------------------------------------
def test_attention_is_read_only_context_not_an_output_field() -> None:
    """attention 은 프롬프트에 들어가되(읽기), 출력 스키마엔 없다(쓰기 불가)."""
    cohort = [
        StockContext(
            ticker="005930",
            name="삼성전자",
            evidence={"demand_search_series_recent_60d": []},
            attention={"z": 3.2, "tier": "급증", "meaning": "방향 정보 아님"},
        )
    ]
    prompt = build_prompt("DATALAB", "2026-06-01", cohort)
    # 읽기: 컨텍스트로는 들어간다
    assert "급증" in prompt
    assert "3.2" in prompt
    # 규범: 급증을 방향 근거로 쓰지 말라고 못박혀 있다
    assert "방향 근거가 아니다" in prompt
    # 쓰기: 출력 스키마엔 attention 필드가 없다
    scored = parse_scores(
        {"scores": [{"ticker": "005930", "score": 0.0, "confidence": 0.3, "no_signal": True,
                     "evidence": ["구성 변화를 측정할 수 없음"]}]},
        cohort,
    )
    assert not hasattr(scored[0], "attention")
    assert scored[0].no_signal is True
    assert scored[0].score == 0.0  # no_signal 이면 점수는 강제로 0


def test_no_signal_forces_zero_score() -> None:
    """LLM 이 no_signal 과 함께 0 아닌 점수를 보내도 코드가 0으로 강제한다."""
    scored = parse_scores(_payload(no_signal=True, score=0.9), COHORT)
    assert scored[0].no_signal is True
    assert scored[0].score == 0.0


# --- 2. confidence 상한 -------------------------------------------------------------
def test_confidence_is_clamped_by_code_not_by_prompt() -> None:
    scored = parse_scores(_payload(confidence=0.99), COHORT)
    assert scored[0].confidence == CONFIDENCE_CAP == 0.85


def test_negative_confidence_clamped_to_zero() -> None:
    scored = parse_scores(_payload(confidence=-1.0), COHORT)
    assert scored[0].confidence == 0.0


# --- 3. 투자권유 차단 ---------------------------------------------------------------
def test_rejects_investment_advice_in_evidence() -> None:
    with pytest.raises(LlmScorerError, match="investment advice"):
        parse_scores(_payload(evidence=["지금 매수 추천드립니다"]), COHORT)


def test_allows_factual_evidence_that_mentions_target_price() -> None:
    """REPORT 는 주제가 목표주가다 — 사실 서술은 통과해야 한다."""
    scored = parse_scores(_payload(evidence=["목표주가가 12만원으로 상향됐다"]), COHORT)
    assert scored[0].evidence == ["목표주가가 12만원으로 상향됐다"]


# --- 계약 위반 --------------------------------------------------------------------
def test_rejects_score_out_of_range() -> None:
    with pytest.raises(LlmScorerError, match="out of range"):
        parse_scores(_payload(score=1.4), COHORT)


def test_rejects_missing_cohort_member() -> None:
    """코호트 종목을 빠뜨리면 거부 — 조용히 일부만 채점되면 안 된다."""
    with pytest.raises(LlmScorerError, match="missing cohort ticker"):
        parse_scores({"scores": [{"ticker": "005930", "score": 0.1, "confidence": 0.5}]}, COHORT)


def test_rejects_malformed_payload() -> None:
    with pytest.raises(LlmScorerError, match="missing 'scores'"):
        parse_scores({"nope": []}, COHORT)


# --- 코호트 배치 --------------------------------------------------------------------
def test_prompt_contains_whole_cohort_for_relative_scoring() -> None:
    prompt = build_prompt("HIRING", "2026-06-01", COHORT)
    assert "005930" in prompt and "000660" in prompt
    assert "상대" in prompt  # 상대 채점 지시
    assert "전원 중립도 정답" in prompt  # 강제 분산 금지 가드


def test_score_cohort_single_call_for_whole_cohort() -> None:
    """코호트 전체가 **한 번의 호출**로 처리된다(비용이 코호트 크기만큼 내려가는 근거)."""

    class _FakeLlm:
        model = "fake"
        calls = 0

        async def generate_json(self, prompt: str):
            type(self).calls += 1
            return _payload()

    client = _FakeLlm()
    scored = asyncio.run(
        score_cohort(client, source="HIRING", asof="2026-06-01", cohort=COHORT)
    )
    assert _FakeLlm.calls == 1
    assert [s.ticker for s in scored] == ["005930", "000660"]
    assert scored[0].direction == "positive"   # +0.4 >= 0.2
    assert scored[1].direction == "negative"   # -0.3 <= -0.2
