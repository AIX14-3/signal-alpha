"""Shared policy copy-safety terms for worker LLM boundaries.

투자권유 금지는 **컴플라이언스 안전장치**다. LLM이 점수를 소유하게 되면 "상승할 것" 류의
표현을 자유롭게 쓸 수 있어 이 리스크는 오히려 커진다 — 따라서 유지한다.

## directive-only 원칙 (중요)
과거 ``analyzers/dart/llm.py`` 는 **"매수"·"매도"·"보유"·"목표가"를 맨 부분문자열로** 차단했다.
그 결과:

- ``보유``  → 지분 공시마다 등장("5% 이상 **보유**") → DART 정상 출력이 거부됨
- ``매수``  → "외국인 순**매수**", "**매수**세 유입" 같은 *사실 서술*이 거부됨
- ``목표가`` → REPORT 는 **주제 자체가 목표주가**라 모든 정상 출력이 거부됨

즉 필터가 사실 묘사를 투자권유로 오인했다. 이 모듈은 그 교훈을 반영해 **지시(directive)
표현만** 차단한다: 사실을 말하는 것("목표주가가 12만원으로 상향됐다")은 통과시키고, 행동을
지시하는 것("매수 추천", "비중 확대")만 막는다.

``narrate/base.py`` · ``synthesis/synthesizer.py`` · 분석기 LLM 경로가 모두 이 단일 소스를
쓴다(파리티 보장 — 과거엔 채널마다 필터 강도가 달라 지시 표현이 약한 채널로 새어나갔다).
"""

from __future__ import annotations

import re

POLICY_RECOMMENDATION_PHRASES: tuple[str, ...] = (
    "보유 추천",
    "목표 수익률",
    "수익 예측",
    "투자 타이밍 알림",
)


def contains_policy_recommendation(text: str) -> bool:
    return any(phrase in text for phrase in POLICY_RECOMMENDATION_PHRASES)


# 지시 표현 정규식. 사실 서술("순매도", "매수세", "보유 지분")은 걸리지 않는다.
ADVICE_REGEXES: tuple[re.Pattern[str], ...] = (
    # bare 영어 동사 — 한국어 발행물에 나오면 사실상 권유다.
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    # 매수/매도 + 지시 어미. "순매도"·"매도세" 는 앞에 다른 글자가 붙어 있어도 여기선
    # 뒤따르는 지시 어미가 없으므로 통과한다.
    re.compile(r"매[수도]\s*(?:추천|권장|권유|의견|하세요|하십시오|하라|해야|하시|하시기|하는\s*것이\s*좋)"),
    re.compile(r"매[수도](?:를|을)\s*(?:추천|권유|권)"),
    re.compile(r"적극\s*매[수도]"),
    re.compile(r"비중\s*(?:을|를)?\s*(?:확대|축소|늘리|줄이)"),
    re.compile(r"담으(?:세요|십시오)"),
    re.compile(r"들어가도\s*좋"),
)

# 명백한 권유 어구(부분문자열). 사실 서술로 쓰일 여지가 없는 것만 넣는다.
# ⚠️ "목표가"/"목표주가" 는 **넣지 않는다** — REPORT 의 주제이자 사실 서술이다.
#    ("목표 수익률" 은 우리가 수익을 약속하는 표현이라 POLICY_RECOMMENDATION_PHRASES 에 남는다)
# ⚠️ bare "매수"/"매도"/"보유" 도 **넣지 않는다** — 순매수/매도세/지분 보유가 전부 사실 서술이다.
ADVICE_TERMS: tuple[str, ...] = (
    "투자 추천",
    "투자추천",
    "추천합니다",
    "추천드립니다",
    "사세요",
    "파세요",
    "매수의견",
    "매도의견",
    "비중 확대",
    "비중 축소",
    "적극 매수",
    "적극 매도",
)


def find_investment_advice(text: str) -> str | None:
    """투자권유 표현을 찾으면 그 근거(패턴/어구)를 돌려주고, 없으면 None.

    호출측은 반환값이 not None 이면 자기 예외 타입으로 승격한다(에러 타입이 채널마다 달라
    공용 함수가 raise 하지 않는다).
    """
    for rx in ADVICE_REGEXES:
        match = rx.search(text)
        if match:
            return match.group(0)
    for term in ADVICE_TERMS:
        if term in text:
            return term
    for phrase in POLICY_RECOMMENDATION_PHRASES:
        if phrase in text:
            return phrase
    return None


def find_investment_advice_in(values: list[str]) -> str | None:
    """``find_investment_advice`` 의 리스트 버전(문자열들을 이어붙여 검사)."""
    return find_investment_advice(" ".join(values))
