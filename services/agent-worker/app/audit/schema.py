"""발행물 감사 결과 계약 — verdict/claim/flag dataclass.

수치는 담지 않는다(감사는 flag 만). ``PublicationAuditVerdict`` 는 발행물의 ``publication_audit`` 필드에
그대로 실린다 — score/direction/서술 원본은 불변.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# 사실 주장(수치·이벤트) vs 평가/전망 표현(규제 회색지대 후보).
ClaimType = Literal["factual", "evaluative"]
# 위반 종류 — regulation(투자 함의/조언 소지) | grounding(데이터와 불일치=환각) |
# unverified(근거 부족으로 확인 못 함 ≠ 환각; grounding_ok/compliant 를 내리지 않음).
FlagType = Literal["regulation", "grounding", "unverified"]
# 판정 결과 — ok(중립 사실) | regulation | grounding | insufficient(근거 부족·보류 → 에스컬레이션 트리거).
AdjudicationVerdict = Literal["ok", "regulation", "grounding", "insufficient"]


@dataclass(frozen=True)
class Claim:
    """서술에서 뽑은 검증 대상 한 조각."""

    span: str  # 서술 원문 구간(그대로)
    claim_type: ClaimType
    subject: str | None = None  # 무엇에 대한 주장인지(선택, grounding 근거 조회 힌트)


@dataclass(frozen=True)
class AuditFlag:
    """감사가 잡은 위반/불일치 하나 — 근거·사유 포함. 수치 안 바꾸고 flag 만."""

    span: str
    type: FlagType
    reason: str
    evidence: str | None = None  # 대조에 쓴 근거(grounding 이면 데이터, 있으면)


@dataclass(frozen=True)
class Adjudication:
    """주장 1건에 대한 LLM 판정 결과(내부용). flag 생성은 agent 루프가 담당.

    ``verdict == "insufficient"`` 이면 ``need``(무슨 근거가 있으면 확정되는지)를 근거로 에스컬레이션한다.
    """

    verdict: AdjudicationVerdict
    reason: str = ""
    evidence: str | None = None
    need: str | None = None


@dataclass(frozen=True)
class PublicationAuditVerdict:
    """발행물 감사 결과 — ``publication_audit`` 필드로 실린다.

    ``compliant``/``grounding_ok`` 는 각각 regulation/grounding flag 부재로 정의. ``trace`` 는 판정 경로
    (데모·설명용). ``degraded`` = LLM 미구성/실패로 정적 필터만 돌았는지(발행은 계속).
    """

    compliant: bool
    grounding_ok: bool
    flags: list[AuditFlag] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_ver: str = "pub-audit-v1"
    degraded: bool = False
    hops: int = 0  # 에스컬레이션 총 재조회 홉 수(가시화·데모용)
