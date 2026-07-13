"""공용 투자권유 가드(directive-only) — 5개 LLM 채널의 단일 소스.

과거엔 채널마다 필터 사본이 있었고 강도가 제각각이었다:
  - ``analyzers/dart/llm.py``            : "매수"·"매도"·"보유"·"목표가" **맨 부분문자열** 차단
  - ``collectors/report/.../valuation_llm.py`` : 같은 문제 (REPORT 수집 경로)
  - ``synthesis/synthesizer.py``          : directive-only 이나 "목표가" 는 여전히 차단
  - ``narrate/base.py``                   : 동상

그 결과 (a) REPORT 는 주제가 목표주가라 **모든 정상 출력이 거부**됐고, (b) 지분 공시의
"5% 이상 보유", 시장 서술의 "외국인 순매도"까지 투자권유로 오인됐다. 이 테스트는 통합 후의
계약을 고정한다: **사실 서술은 통과, 행동 지시만 차단.**
"""

from __future__ import annotations

import pytest

from app.policy_safety import find_investment_advice, find_investment_advice_in

# --- 차단돼야 하는 것: 행동을 지시하는 표현 ---------------------------------------
BLOCKED = [
    "지금 매수하세요",
    "매수 추천드립니다",
    "매도 추천",
    "매수를 권유합니다",
    "적극 매수 구간입니다",
    "비중 확대가 필요합니다",
    "비중을 줄이십시오",
    "지금 담으세요",
    "들어가도 좋습니다",
    "투자 추천 종목입니다",
    "매수의견을 유지합니다",
    "Strong buy signal",
    "target price raised",
    "목표 수익률 20%를 제시합니다",
]

# --- 통과해야 하는 것: 사실 서술 ---------------------------------------------------
# 하나라도 걸리면 해당 소스의 정상 출력이 통째로 막힌다(과거 REPORT 가 그랬다).
ALLOWED = [
    # REPORT — 주제 자체가 목표주가다
    "증권사 목표주가가 12만원으로 상향 조정되었습니다.",
    "목표가 대비 현재가 괴리율은 18%입니다.",
    "3개 증권사가 목표주가를 하향했습니다.",
    # DART — 지분 공시엔 '보유'가 반드시 등장한다
    "국민연금이 지분 5% 이상을 보유하고 있습니다.",
    "자기주식 취득 후 보유 물량이 증가했습니다.",
    "최대주주의 보유 지분율이 42%로 변동 없습니다.",
    # PRICE — 수급 서술엔 '매수/매도'가 반드시 등장한다
    "외국인 순매도가 5거래일 연속 이어졌습니다.",
    "기관 매수세가 유입되었습니다.",
    "매도 우위 흐름이 지속되고 있습니다.",
    # 중립 서술
    "검색량이 평소 대비 3배 증가했습니다.",
    "채용 공고가 전분기 대비 30% 늘었습니다.",
]


@pytest.mark.parametrize("text", BLOCKED)
def test_blocks_directive_advice(text: str) -> None:
    assert find_investment_advice(text) is not None, f"차단됐어야 함: {text!r}"


@pytest.mark.parametrize("text", ALLOWED)
def test_allows_factual_statements(text: str) -> None:
    hit = find_investment_advice(text)
    assert hit is None, f"사실 서술인데 차단됨: {text!r} (걸린 패턴: {hit!r})"


def test_list_variant_joins_values() -> None:
    assert find_investment_advice_in(["정상 문장", "지금 매수하세요"]) is not None
    assert find_investment_advice_in(["목표주가 상향", "외국인 순매도"]) is None


def test_returns_the_matched_pattern_for_diagnostics() -> None:
    """반환값이 '무엇 때문에 막혔는지'를 알려줘야 운영 디버깅이 된다."""
    assert find_investment_advice("투자 추천 종목") == "투자 추천"
