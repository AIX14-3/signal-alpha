"""발행물 컴플라이언스·사실 grounding 감사 agent.

LLM 이 생성한 서술(SYNTHESIZE 산출물)을 발행 직전에 독립 감사한다 — (a) 규제 안전(투자 조언·방향 함의
없음) + (b) 사실 grounding(우리가 계산한 데이터에 근거, 환각 없음). 이슬님 축(신호 *생성*·needs_review)과
직교하는 *출력 감사*. 점수/방향/서술은 절대 안 바꾸고 ``publication_audit`` verdict 만 flag 로 산출한다.

설계 규율: 쉬운 위반(bare buy/sell)은 정적 필터(``narrate.base.reject_advice``)가 잡고, agent 는 그게 못 잡는
경계 표현·사실 주장에만 붙는다 — 근거 재조회+맥락 판정 루프가 존재 이유(검사기 함정 회피).
"""

from app.audit.agent import (
    MAX_ESCALATION_HOPS,
    PROMPT_VERSION,
    EvidenceProvider,
    PublicationAuditAgent,
)
from app.audit.schema import Adjudication, AuditFlag, Claim, PublicationAuditVerdict

__all__ = [
    "PublicationAuditAgent",
    "EvidenceProvider",
    "PROMPT_VERSION",
    "MAX_ESCALATION_HOPS",
    "PublicationAuditVerdict",
    "Adjudication",
    "AuditFlag",
    "Claim",
]
