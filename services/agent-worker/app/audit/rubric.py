"""정적 1차 필터 + LLM 프롬프트 빌더/파서.

정적 필터는 ``narrate.base.reject_advice`` 를 span 단위로 재사용한다(regex 조언셋 + 정책문구까지 이미 커버 —
중복 정의 없음). LLM 프롬프트는 GeminiJsonClient(JSON 계약)용. 프롬프트 본문은 v1 스켈레톤으로 인라인이며,
정교화 시 ``app/prompts/*.md`` 로 이관(narrate 관례)한다.
"""

from __future__ import annotations

import json
from typing import Any

from app.audit.schema import Adjudication, AuditFlag, Claim
from app.narrate.base import NarrateError, reject_advice


def static_screen(spans: list[str]) -> list[AuditFlag]:
    """명시 위반(bare buy/sell·비중확대·목표가·정책문구 등)을 span 단위로 즉시 flag. LLM 불필요.

    ``reject_advice`` 를 재사용 — 동급 규제셋이라 위반이면 NarrateError → regulation flag. 쉬운 케이스를
    여기서 걸러 agent 의 LLM 호출을 경계·사실주장으로만 최소화한다.
    """
    flags: list[AuditFlag] = []
    for span in spans:
        try:
            reject_advice([span])
        except NarrateError as exc:
            flags.append(
                AuditFlag(span=span, type="regulation", reason=f"명시 규제 위반: {exc}")
            )
    return flags


def build_extract_prompt(narrative: str) -> str:
    """서술 → 검증 대상 주장 목록(JSON). factual(수치·이벤트) / evaluative(평가·전망)."""
    return (
        "다음은 투자 신호 리포트의 한 소스 서술이다. 검증이 필요한 '주장'만 뽑아라.\n"
        "- factual: 수치·건수·이벤트 등 데이터로 대조 가능한 사실 주장.\n"
        "- evaluative: '유망', '성장 동력' 등 평가·전망 표현(투자 함의 소지 후보).\n"
        "JSON 배열만 출력: [{\"span\": 원문구간, \"claim_type\": \"factual|evaluative\", "
        "\"subject\": 대상(선택)}].\n\n"
        f"[서술]\n{narrative}\n"
    )


def build_adjudicate_prompt(claim: Claim, evidence: dict[str, Any]) -> str:
    """주장 1건을 근거 대조로 판정(JSON). verdict: ok|regulation|grounding.

    규제(regulation): 사실 서술을 넘어 투자 함의·조언 소지가 있나. grounding: 서술이 아래 근거 데이터와
    불일치(환각)하나. 둘 다 아니면 ok. **판정도 규제 준수** — '매수/매도 신호'를 내리지 말고 위반 소지만 flag.
    """
    evidence_json = json.dumps(evidence, ensure_ascii=False, default=str)
    return (
        "너는 발행 리포트의 컴플라이언스·사실 감사자다. 아래 '주장'을 '근거 데이터'와 대조해 판정하라.\n"
        "- regulation: 사실 서술을 넘어 투자 함의/조언 소지(방향성 권유·수익 암시 등)가 있다.\n"
        "- grounding: 주장이 근거 데이터와 불일치한다(환각·수치 어긋남).\n"
        "- insufficient: 근거 데이터가 부족/불충분해 확정할 수 없다. 이때 'need' 에 무슨 근거가 있으면\n"
        "  확정 가능한지 짧게 적어라(예: '해당 종목 실제 공고 건수'). 근거 없이 통과시키지 말 것.\n"
        "- ok: 사실에 근거한 중립 서술.\n"
        "너는 매수/매도 판단을 내리지 않는다 — 위반 '소지'만 flag 한다.\n"
        'JSON 객체만 출력: {"verdict": "ok|regulation|grounding|insufficient", "reason": 사유, '
        '"evidence": 대조근거, "need": insufficient일 때 필요한 근거}.\n\n'
        f"[주장] ({claim.claim_type}) {claim.span}\n"
        f"[근거 데이터]\n{evidence_json}\n"
    )


def parse_adjudication(data: Any) -> Adjudication:
    """판정 응답(JSON) → Adjudication. verdict 불명/누락은 보수적으로 ``insufficient``.

    (unknown → insufficient: 파싱 실패를 조용한 통과(ok)로 두면 감사가 무력화되고, grounding 로 두면
    위양성이 된다. insufficient 는 에스컬레이션/unverified 로 흘러 '확인 못 함'을 정직하게 남긴다.)
    """
    obj = data if isinstance(data, dict) else {}
    raw = str(obj.get("verdict") or "").strip().lower()
    verdict = raw if raw in ("ok", "regulation", "grounding", "insufficient") else "insufficient"
    evidence = str(obj.get("evidence") or "").strip() or None
    need = str(obj.get("need") or "").strip() or None
    return Adjudication(
        verdict=verdict,  # type: ignore[arg-type]  # 위 검증으로 좁혀짐
        reason=str(obj.get("reason") or "").strip(),
        evidence=evidence,
        need=need,
    )


def parse_claims(data: Any) -> list[Claim]:
    """추출 프롬프트 응답(JSON) → Claim 리스트. list 또는 {\"claims\": [...]} 양형 허용, 방어적 파싱."""
    items = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    claims: list[Claim] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        span = str(item.get("span") or "").strip()
        if not span:
            continue
        claim_type = "factual" if item.get("claim_type") == "factual" else "evaluative"
        subject = str(item.get("subject")).strip() if item.get("subject") else None
        claims.append(Claim(span=span, claim_type=claim_type, subject=subject))
    return claims
