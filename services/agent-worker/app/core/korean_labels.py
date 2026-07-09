"""내부 코드 → 사용자에게 보여 줄 한국어 라벨.

집계기·합성기·분석기가 만드는 문장은 LLM 서술이 없을 때 **그대로 화면에 나간다**
(``_deterministic_narrative``/``_summary`` 폴백). 그런데 여러 곳에서 ``mixed``,
``CAUTION``, ``DART``, ``{'neutral': 1}`` 같은 내부 표현이 문장에 새어 나왔다.
라벨을 한 곳에 모아 두면 문구가 갈라지지 않는다.

숫자·판정은 여기서 다루지 않는다 — 표기(表記)만 담당한다.
"""

from __future__ import annotations

from collections import Counter

# final_signals.signal / SourceResult.direction
SIGNAL_KO = {
    "positive": "긍정",
    "negative": "주의",
    "neutral": "중립",
    "mixed": "엇갈리는",
    "unknown": "판단 보류",
}

# score_breakdown 키 / signal_events.source_type
SOURCE_KO = {
    "DART": "공시",
    "PRICE": "주가",
    "REPORT": "증권사 리포트",
    "HIRING": "채용공고",
    "PATENT": "특허",
    "DATALAB": "검색량",
}

# final_signals.warning_level
WARNING_KO = {
    "NORMAL": "특별한 주의 사항 없는",
    "CAUTION": "주의가 필요한",
    "WARNING": "각별한 주의가 필요한",
}

# signal_events.event_type (DART)
DART_EVENT_TYPE_KO = {
    "correction": "정정공시",
    "dart_disclosure": "일반공시",
    "major_disclosure": "주요사항보고",
    "material_event": "주요사항보고",
    "insider_ownership": "임원·주요주주 지분변동",
    "periodic_report": "정기보고서",
    "governance_report": "지배구조보고서",
    "treasury_disposal": "자기주식 처분",
    "disclosure": "공시",
}


def counts_text(counts: Counter | dict[str, int], labels: dict[str, str]) -> str:
    """Counter → "정기보고서 2건, 정정공시 1건" (많은 순).

    ``dict(Counter)`` 를 f-string 에 그대로 끼워 넣으면 ``{'correction': 1}`` 같은
    파이썬 repr 이 화면에 노출된다 — 문장을 만들 땐 항상 이 함수를 거친다.
    """
    parts = [
        f"{labels.get(key, key)} {count}건"
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return ", ".join(parts)
