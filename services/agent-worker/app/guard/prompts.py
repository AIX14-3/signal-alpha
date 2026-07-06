"""지정학 리스크 판정 프롬프트 — 버전 태깅으로 회귀 추적."""

from __future__ import annotations

import json
from typing import Any

GUARD_PROMPT_VERSION = "geo-risk-v1"

_TEMPLATE = """당신은 지정학 리스크 분석가입니다. 아래 뉴스 묶음을 읽고, 한국 주식시장에 미칠
지정학적 충격을 평가해 "엄격한 JSON"으로만 답하세요.

규칙:
- 투자 조언(매수/매도/보유/목표가 등)을 절대 쓰지 마세요.
- 근거가 약하면 confidence 를 낮추고 severity 를 보수적으로 매기세요.
- 한국 시장과 무관한 뉴스 묶음이면 is_geopolitical_risk 를 false 로 두세요.

출력 스키마(모든 필드 필수):
{{
  "severity": 0-100 정수,
  "is_geopolitical_risk": true/false,
  "direction": "escalation" | "deescalation" | "unclear",
  "summary": "1~2문장 한국어 요약(차단 안내 화면 사유 후보)",
  "regions": ["관련 지역"],
  "affected_themes": ["영향 테마"],
  "confidence": 0-100 정수,
  "evidence": ["판단 근거 문장"]
}}

예시 1 (확전):
입력: 미군 기지 추가 타격 발표, 호르무즈 해협 봉쇄 우려, 유가 9% 급등
출력: {{"severity": 88, "is_geopolitical_risk": true, "direction": "escalation",
 "summary": "이란-미국 무력 충돌 확전으로 호르무즈 해협 봉쇄 우려가 커지고 있습니다.",
 "regions": ["Middle East", "Iran", "US"], "affected_themes": ["oil", "defense", "shipping"],
 "confidence": 76, "evidence": ["미군 기지 추가 타격 발표", "유가 9% 급등"]}}

예시 2 (완화):
입력: 휴전 합의 서명, 국경 병력 철수 시작
출력: {{"severity": 25, "is_geopolitical_risk": true, "direction": "deescalation",
 "summary": "휴전 합의로 지정학 긴장이 완화되고 있습니다.",
 "regions": ["Middle East"], "affected_themes": ["oil"],
 "confidence": 70, "evidence": ["휴전 합의 서명", "병력 철수 시작"]}}

예시 3 (무관):
입력: 신형 스마트폰 출시 행사, 분기 실적 발표
출력: {{"severity": 0, "is_geopolitical_risk": false, "direction": "unclear",
 "summary": "지정학 리스크와 무관한 뉴스입니다.",
 "regions": [], "affected_themes": [], "confidence": 90, "evidence": []}}

입력 뉴스 묶음:
{input_json}
"""


def build_prompt(articles: list[dict[str, Any]]) -> str:
    return _TEMPLATE.format(input_json=json.dumps(articles, ensure_ascii=False, indent=1))
