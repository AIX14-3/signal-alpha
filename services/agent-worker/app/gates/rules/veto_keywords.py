"""치명 키워드 — 리스크 veto가 발행을 보류시키는 사유.

architecture.mermaid의 "리스크 veto (치명 키워드 차단)". 한 종목의 증거 텍스트(공시 제목/요약
등)에 아래 키워드가 보이면, 점수/방향과 무관하게 신호 발행을 보류한다(상장폐지·감사의견거절
같은 사건은 신호의 의미를 무력화하므로 모델 점수보다 우선하는 결정론적 차단).

기본 목록은 한국 시장의 대표적 치명 이벤트다. ``RISK_VETO_KEYWORDS`` (콤마 구분) 환경변수로
추가 키워드를 더할 수 있다(기본 목록에 가산).
"""

from __future__ import annotations

import os

# 기본 치명 키워드(부분 문자열 매칭). 한국어 공시/뉴스 표현 기준.
DEFAULT_VETO_KEYWORDS: tuple[str, ...] = (
    "상장폐지",
    "상장 폐지",
    "관리종목",
    "관리 종목",
    "투자주의환기종목",
    "불성실공시법인",
    "감사의견거절",
    "의견거절",
    "감사범위제한",
    "부적정의견",
    "한정의견",
    "거래정지",
    "매매거래정지",
    "자본잠식",
    "회생절차",
    "기업회생",
    "법정관리",
    "파산",
    "횡령",
    "배임",
    "분식회계",
    "감사보고서 미제출",
)


def veto_keywords() -> list[str]:
    """기본 치명 키워드 + ``RISK_VETO_KEYWORDS`` 환경변수 가산(중복 제거, 순서 보존)."""
    keywords = list(DEFAULT_VETO_KEYWORDS)
    extra = os.getenv("RISK_VETO_KEYWORDS")
    if extra:
        keywords.extend(item.strip() for item in extra.split(",") if item.strip())
    seen: set[str] = set()
    deduped: list[str] = []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            deduped.append(keyword)
    return deduped
