"""소스별 LLM 서술(narrate) 라인 — 파싱데이터 + 예측률을 묶어 읽기 쉬운 한국어 서술 생성.

C안 정합: LLM 은 **서술(summary/key_facts)만** 산출하고 방향·점수 수치는 바꾸지 않는다(숫자는
메타러너 src_* 예측률). 각 소스(DART/REPORT/PRICE)는 독립 narrator 모듈이며 플래그로 개별 게이팅한다.
"""

from app.narrate.base import NarrateError, SourceNarrative, build_narrate_client

__all__ = ["NarrateError", "SourceNarrative", "build_narrate_client"]
