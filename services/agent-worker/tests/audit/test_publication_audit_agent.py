"""PublicationAuditAgent 판정 루프 + 에스컬레이션 검증.

config 의존 없이 ``asyncio.run`` 으로 구동. 커버: 정적 필터가 명시 조언을 잡는가 · LLM 없으면 degrade 하며
발행 안 막는가 · LLM grounding flag · 개별 판정 실패 격리 · ok 판정 무플래그 · **에스컬레이션(1홉 재조회 후
grounding 확정 · always-insufficient 상한 정지 · provider 없음 즉시 unverified)**.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.audit.agent import MAX_ESCALATION_HOPS, PublicationAuditAgent
from app.audit.schema import Claim
from app.clients.gemini_client import GeminiError


class _FakeClient:
    """generate_json 을 프롬프트 종류로 라우팅하는 스텁. adjudicate 프롬프트에만 '감사자' 가 들어간다.

    ``adjudicate`` 는 단일 dict · Exception · **list(홉마다 순차 소비)** 를 받는다(에스컬레이션 시퀀스 검증용).
    """

    def __init__(self, *, extract: Any, adjudicate: Any, model: str = "fake-model") -> None:
        self._extract = extract
        self._adjudicate = adjudicate
        self.adjudicate_calls = 0
        self.model = model

    async def generate_json(self, prompt: str) -> Any:
        if "감사자" in prompt:  # build_adjudicate_prompt
            self.adjudicate_calls += 1
            adj = self._adjudicate
            if isinstance(adj, list):
                adj = adj[min(self.adjudicate_calls - 1, len(adj) - 1)]
            if isinstance(adj, Exception):
                raise adj
            return adj
        return self._extract  # build_extract_prompt


class _FakeProvider:
    """insufficient 재조회 콜백 스텁 — 호출 수를 세고 고정 근거를 병합한다."""

    def __init__(self, extra: dict | None = None) -> None:
        self.calls = 0
        self._extra = extra or {}

    async def __call__(self, claim: Claim, need: str | None) -> dict[str, Any]:
        self.calls += 1
        return dict(self._extra)


def _audit(agent: PublicationAuditAgent, narrative: str, evidence: dict | None = None):
    return asyncio.run(agent.audit(narrative=narrative, evidence=evidence or {}))


# ── 기존 5케이스 (무변) ──────────────────────────────────────────────────────


def test_static_screen_flags_explicit_advice_without_llm():
    agent = PublicationAuditAgent(client=None)
    verdict = _audit(agent, "이 종목을 적극 매수 추천합니다.")
    assert verdict.degraded is True
    assert verdict.compliant is False
    assert any(f.type == "regulation" for f in verdict.flags)


def test_clean_narrative_no_client_degrades_but_passes():
    agent = PublicationAuditAgent(client=None)
    verdict = _audit(agent, "채용 공고가 직전 대비 3건 늘었다.")
    assert verdict.degraded is True
    assert verdict.compliant is True
    assert verdict.grounding_ok is True
    assert verdict.flags == []


def test_grounding_flag_via_llm():
    client = _FakeClient(
        extract=[{"span": "채용 30건으로 크게 늘었다", "claim_type": "factual"}],
        adjudicate={"verdict": "grounding", "reason": "실데이터는 3건", "evidence": "3건"},
    )
    agent = PublicationAuditAgent(client=client)
    verdict = _audit(agent, "채용 30건으로 크게 늘었다.", {"HIRING": {"count": 3}})
    assert verdict.degraded is False
    assert verdict.grounding_ok is False
    assert verdict.model == "fake-model"
    grounding = [f for f in verdict.flags if f.type == "grounding"]
    assert len(grounding) == 1
    assert grounding[0].evidence == "3건"


def test_adjudicate_failure_is_isolated():
    client = _FakeClient(
        extract=[{"span": "성장 동력이 강하다", "claim_type": "evaluative"}],
        adjudicate=GeminiError("boom"),
    )
    agent = PublicationAuditAgent(client=client)
    verdict = _audit(agent, "성장 동력이 강하다.")
    assert verdict.flags == []
    assert verdict.compliant is True
    assert any("판정 실패 격리" in t for t in verdict.trace)


def test_ok_verdict_yields_no_flag():
    client = _FakeClient(
        extract=[{"span": "채용이 소폭 증가했다", "claim_type": "factual"}],
        adjudicate={"verdict": "ok", "reason": "데이터와 일치"},
    )
    agent = PublicationAuditAgent(client=client)
    verdict = _audit(agent, "채용이 소폭 증가했다.", {"HIRING": {"count": 5}})
    assert verdict.compliant is True
    assert verdict.grounding_ok is True
    assert verdict.flags == []


# ── 신규 3케이스: 에스컬레이션(hop = 재조회 횟수) ────────────────────────────


def test_escalation_resolves_to_grounding_after_refetch():
    client = _FakeClient(
        extract=[{"span": "채용 30건", "claim_type": "factual"}],
        adjudicate=[
            {"verdict": "insufficient", "need": "실제 공고 건수"},
            {"verdict": "grounding", "reason": "실데이터 3건", "evidence": "3건"},
        ],
    )
    provider = _FakeProvider(extra={"HIRING": {"count": 3}})
    agent = PublicationAuditAgent(client=client, evidence_provider=provider)
    verdict = _audit(agent, "채용 30건.", {})
    assert provider.calls == 1  # 정확히 1홉 재조회
    assert verdict.hops == 1
    assert verdict.grounding_ok is False
    assert any(f.type == "grounding" for f in verdict.flags)
    assert any("에스컬레이션 hop1" in t for t in verdict.trace)


def test_always_insufficient_stops_at_cap_as_unverified():
    client = _FakeClient(
        extract=[{"span": "성장 동력이 강하다", "claim_type": "evaluative"}],
        adjudicate={"verdict": "insufficient", "need": "원본 데이터"},  # 항상 insufficient
    )
    provider = _FakeProvider()
    agent = PublicationAuditAgent(client=client, evidence_provider=provider)
    verdict = _audit(agent, "성장 동력이 강하다.")
    assert provider.calls == MAX_ESCALATION_HOPS  # 상한서 정지 — 무한루프 없음
    unverified = [f for f in verdict.flags if f.type == "unverified"]
    assert len(unverified) == 1
    # unverified 는 grounding_ok/compliant 를 내리지 않는다.
    assert verdict.grounding_ok is True
    assert verdict.compliant is True


def test_insufficient_without_provider_is_immediate_unverified():
    client = _FakeClient(
        extract=[{"span": "채용이 늘었다", "claim_type": "factual"}],
        adjudicate={"verdict": "insufficient", "need": "건수"},
    )
    agent = PublicationAuditAgent(client=client, evidence_provider=None)
    verdict = _audit(agent, "채용이 늘었다.")
    assert verdict.hops == 0  # provider 없음 → 재조회 0홉
    assert any(f.type == "unverified" for f in verdict.flags)
    assert verdict.grounding_ok is True
