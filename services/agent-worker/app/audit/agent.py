"""발행물 감사 agent — evidence-grounded adjudication 루프(에스컬레이션 포함).

루프: 주장 추출(LLM) → 정적 1차 필터(reject_advice 재사용) → 경계·사실주장만 근거 대조 판정(LLM).
판정이 ``insufficient`` 면 ``evidence_provider`` 로 **타깃 근거를 재조회해 재판정**한다(bounded 다단계 —
검사기가 아닌 agent 인 이유). ``MAX_ESCALATION_HOPS`` 로 상한(무한 reflection 금지).

불변식: 점수/방향/서술 원본 불변(``publication_audit`` verdict 만 flag). LLM 미구성/실패/에스컬레이션 소진 모두
degrade 후 통과(발행 안 막음). 개별 판정·재조회 실패는 격리하고 감사 계속. 미해결(insufficient 소진)은
``unverified`` — "확인 못 함" ≠ "환각"이라 grounding_ok/compliant 를 내리지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from app.audit.rubric import (
    build_adjudicate_prompt,
    build_extract_prompt,
    parse_adjudication,
    parse_claims,
    static_screen,
)
from app.audit.schema import Adjudication, AuditFlag, Claim, PublicationAuditVerdict
from app.clients.gemini_client import GeminiError, GeminiJsonClient

logger = logging.getLogger(__name__)

PROMPT_VERSION = "pub-audit-v1"
# assemble→adjudicate→재조회→adjudicate 홉 상한(worklog·memory "1홉" 통일). 상한이 종료를 보장한다.
MAX_ESCALATION_HOPS = 1


@runtime_checkable
class EvidenceProvider(Protocol):
    """insufficient 판정 시 타깃 근거를 재조회하는 콜백(DI). None 이면 에스컬레이션 없이 unverified."""

    async def __call__(self, claim: Claim, need: str | None) -> dict[str, Any]: ...


class PublicationAuditAgent:
    """서술을 규제·사실 grounding 관점으로 감사한다. 점수는 절대 안 바꾼다(verdict flag 만)."""

    def __init__(
        self,
        *,
        client: GeminiJsonClient | None,
        evidence_provider: EvidenceProvider | None = None,
    ) -> None:
        self._client = client
        self._evidence_provider = evidence_provider

    async def audit(self, *, narrative: str, evidence: dict[str, Any]) -> PublicationAuditVerdict:
        """서술 + 근거 → 감사 verdict. narrative 가 비면 즉시 통과(감사 대상 없음)."""
        trace: list[str] = []
        if not (narrative or "").strip():
            trace.append("서술 없음 — 감사 대상 없음")
            return self._verdict([], trace, degraded=self._client is None)

        # 1) 주장 추출 (LLM). 없거나 실패면 서술 전체를 한 span 으로 간주.
        claims = await self._extract_claims(narrative, trace)
        spans = [c.span for c in claims] or [narrative]

        # 2) 정적 1차 필터 — 명시 위반 즉시 flag (LLM 불필요).
        flags = static_screen(spans)
        if flags:
            trace.append(f"정적 필터 위반 {len(flags)}건")

        # 3) LLM 미구성 → 정적 결과로 degrade (발행 계속).
        if self._client is None:
            trace.append("LLM 미구성 — 정적 필터만(degraded)")
            return self._verdict(flags, trace, degraded=True)

        # 4) 정적으로 안 잡힌 경계·사실주장만 근거 대조 판정(+에스컬레이션).
        already = {f.span for f in flags}
        total_hops = 0
        for claim in claims:
            if claim.span in already:
                continue
            flag, hops = await self._audit_claim(claim, evidence, trace)
            total_hops += hops
            if flag is not None:
                flags.append(flag)

        return self._verdict(
            flags, trace, degraded=False, model=self._client.model, hops=total_hops
        )

    async def _audit_claim(
        self, claim: Claim, evidence: dict[str, Any], trace: list[str]
    ) -> tuple[AuditFlag | None, int]:
        """주장 1건 판정 + 에스컬레이션. (flag|None, 소비한 재조회 홉수) 반환.

        insufficient → (상한 내에서) evidence_provider 로 타깃 근거 재조회 후 재판정. 상한 소진/ provider 부재/
        재조회 실패는 모두 **unverified**(발행 계속, grounding_ok 안 내림). 판정 GeminiError 는 격리(무플래그).
        """
        ev = dict(evidence)
        hop = 0
        while True:
            adj = await self._adjudicate(claim, ev, trace)
            if adj is None:  # 판정 실패 — 격리(무플래그)
                return None, hop
            if adj.verdict in ("regulation", "grounding"):
                return (
                    AuditFlag(
                        span=claim.span,
                        type=adj.verdict,  # type: ignore[arg-type]  # 위 분기로 좁혀짐
                        reason=adj.reason,
                        evidence=adj.evidence,
                    ),
                    hop,
                )
            if adj.verdict == "ok":
                return None, hop

            # insufficient — 상한/ provider 부재면 unverified 로 마감.
            if hop >= MAX_ESCALATION_HOPS or self._evidence_provider is None:
                trace.append(f"미해결→unverified: {claim.span[:24]}")
                return (
                    AuditFlag(
                        span=claim.span,
                        type="unverified",
                        reason=adj.reason or "근거 부족·미해결",
                        evidence=adj.evidence,
                    ),
                    hop,
                )
            # 타깃 근거 재조회 홉.
            hop += 1
            try:
                extra = await self._evidence_provider(claim, adj.need)
            except Exception as exc:  # noqa: BLE001 — 재조회 실패도 격리 → unverified
                logger.warning("pub-audit 근거 재조회 실패 (span=%r): %s", claim.span, exc)
                trace.append(f"재조회 실패→unverified: {claim.span[:24]}")
                return (
                    AuditFlag(span=claim.span, type="unverified", reason="근거 재조회 실패"),
                    hop,
                )
            ev = {**ev, **(extra or {})}
            trace.append(f"에스컬레이션 hop{hop}: {adj.need or '근거 보강'}")

    async def _extract_claims(self, narrative: str, trace: list[str]) -> list[Claim]:
        if self._client is None:
            return []
        try:
            data = await self._client.generate_json(build_extract_prompt(narrative))
        except GeminiError as exc:
            logger.warning("pub-audit 주장 추출 실패: %s — 서술 전체 대상으로 진행", exc)
            trace.append("주장 추출 실패 — 서술 전체 fallback")
            return []
        claims = parse_claims(data)
        trace.append(f"주장 {len(claims)}건 추출")
        return claims

    async def _adjudicate(
        self, claim: Claim, evidence: dict[str, Any], trace: list[str]
    ) -> Adjudication | None:
        """주장 1건 판정. GeminiError 는 격리(None 반환 → 호출측이 무플래그로 마감)."""
        try:
            data = await self._client.generate_json(build_adjudicate_prompt(claim, evidence))
        except GeminiError as exc:
            logger.warning("pub-audit 판정 실패 (span=%r): %s — 건너뜀", claim.span, exc)
            trace.append(f"판정 실패 격리: {claim.span[:24]}")
            return None
        adj = parse_adjudication(data)
        trace.append(f"판정 {claim.span[:24]}→{adj.verdict}")
        return adj

    def _verdict(
        self,
        flags: list[AuditFlag],
        trace: list[str],
        *,
        degraded: bool,
        model: str | None = None,
        hops: int = 0,
    ) -> PublicationAuditVerdict:
        # unverified 는 어느 쪽도 안 내림 — compliant/grounding_ok 는 각 regulation/grounding flag 부재로만 정의.
        return PublicationAuditVerdict(
            compliant=not any(f.type == "regulation" for f in flags),
            grounding_ok=not any(f.type == "grounding" for f in flags),
            flags=flags,
            trace=trace,
            model=model,
            prompt_ver=PROMPT_VERSION,
            degraded=degraded,
            hops=hops,
        )
