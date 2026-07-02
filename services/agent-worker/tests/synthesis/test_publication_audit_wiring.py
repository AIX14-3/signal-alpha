"""발행물 감사(감사 agent) 배선 v1-lite 게이트 검증.

핵심 안전 보장 = "감사가 off/미구성이면 발행 경로에 무영향(byte-동일 inert)". `_audit_publication` 의 skip 3경로
(비-LLM 서술 · 플래그 off · 키 없음)는 report/narrative 를 건드리기 전에 None 을 반환하므로, LLM·DB·dataclass
없이 그대로 검증한다. (감사 ON 경로는 라이브(run-pipeline)로 검증 — 감사 agent 자체는 tests/audit 8/8.)
"""

from __future__ import annotations

import asyncio

from app.synthesis.tasks import SynthesizeTaskHandler
from tests.synthesis.test_synthesize_handler import (
    _EVENTS,
    _FINAL,
    _FakeConnection,
    _OkSynth,
)


def _handler() -> SynthesizeTaskHandler:
    # connection 은 _audit_publication skip 경로에서 쓰이지 않는다(repo 는 생성만). settings=None → gemini 키 없음.
    return SynthesizeTaskHandler(connection=object(), settings=None)


def _audit(source: str) -> object:
    return asyncio.run(
        _handler()._audit_publication(
            report=None, narrative=None, source=source, score_breakdown=None
        )
    )


def test_audit_skipped_for_non_llm_source(monkeypatch):
    # 플래그가 켜져 있어도 결정론 서술(비-LLM)은 감사 대상 아님 → skip.
    monkeypatch.setenv("PUBLICATION_AUDIT_ENABLED", "true")
    assert _audit("deterministic") is None
    assert _audit("llm_fallback") is None


def test_audit_skipped_when_flag_off(monkeypatch):
    monkeypatch.delenv("PUBLICATION_AUDIT_ENABLED", raising=False)
    assert _audit("llm") is None


def test_audit_degrades_without_key(monkeypatch):
    # 플래그 on + LLM 서술이지만 GEMINI 키 없음 → degrade(None), 발행 계속.
    monkeypatch.setenv("PUBLICATION_AUDIT_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert _audit("llm") is None


# ── ON 경로 불변식 (테스트용 audit_agent 주입 + 실 핸들러 하네스) ──────────────


class _FlaggingAuditAgent:
    """감사가 flag 를 내는 상황 스텁 — 받은 evidence 를 기록."""

    def __init__(self) -> None:
        self.received_evidence = None

    async def audit(self, *, narrative, evidence):
        from app.audit.schema import AuditFlag, PublicationAuditVerdict

        self.received_evidence = evidence
        return PublicationAuditVerdict(
            compliant=False,
            grounding_ok=False,
            flags=[
                AuditFlag(span="유망하다", type="regulation", reason="투자 함의 소지"),
                AuditFlag(span="30건", type="grounding", reason="데이터 불일치", evidence="3건"),
            ],
            trace=["판정 유망하다→regulation", "판정 30건→grounding"],
            model="fake",
            hops=1,
        )


class _UnverifiedAuditAgent:
    """근거 부족 → unverified 만 내는 스텁(grounding_ok/compliant 안 내림)."""

    def __init__(self) -> None:
        self.received_evidence = None

    async def audit(self, *, narrative, evidence):
        from app.audit.schema import AuditFlag, PublicationAuditVerdict

        self.received_evidence = evidence
        return PublicationAuditVerdict(
            compliant=True,
            grounding_ok=True,
            flags=[AuditFlag(span="채용 30건", type="unverified", reason="근거 부족·미해결")],
            trace=["미해결→unverified"],
            model="fake",
            hops=0,
        )


def _run_call(connection, synthesizer, audit_agent):
    handler = SynthesizeTaskHandler(
        connection, settings=None, synthesizer=synthesizer, audit_agent=audit_agent
    )
    return asyncio.run(
        handler(
            {
                "stock_id": 10,
                "source_signal_event_ids": [101],
                "task_context": {"final_signal_id": 55, "stock_code": "005930"},
            }
        )
    )


def test_on_path_flags_but_signal_narrative_publish_unchanged(monkeypatch):
    # 감사가 실제로 돌고 flag 를 내도: 수치/방향/영속 서술 불변 + publish 도달 + publication_audit 만 채움.
    monkeypatch.setenv("PUBLICATION_AUDIT_ENABLED", "true")
    agent = _FlaggingAuditAgent()
    conn = _FakeConnection(dict(_FINAL), list(_EVENTS))
    result = _run_call(conn, _OkSynth(), agent)

    # (a) 수치/방향 불변 + 영속 narrative = LLM 서술 그대로(감사가 안 건드림)
    assert result["report"]["signal"] == "negative"
    assert result["report"]["final_score"] == 22.0
    assert result["narrative_persisted"] is True
    assert conn.narrative_update[0] == 55
    # (b) 핸들러 완주 → publish 경로 도달
    assert "publish_task_id" in result
    # (c) publication_audit 채워짐(수치 채널 아님 — 별 필드)
    pa = result["publication_audit"]
    assert pa is not None
    assert pa["compliant"] is False and pa["grounding_ok"] is False
    assert {f["type"] for f in pa["flags"]} == {"regulation", "grounding"}


def test_on_path_empty_evidence_flows_to_unverified(monkeypatch):
    # 빈 score_breakdown → 조용히 ok 통과가 아니라 unverified 로 흐르고, grounding_ok 는 안 내려간다(L3).
    monkeypatch.setenv("PUBLICATION_AUDIT_ENABLED", "true")
    agent = _UnverifiedAuditAgent()
    conn = _FakeConnection({**_FINAL, "score_breakdown": {}}, list(_EVENTS))
    result = _run_call(conn, _OkSynth(), agent)

    # (i) 빈 evidence 가 감사에 그대로 전달됨
    assert agent.received_evidence is not None
    assert "score_breakdown" in agent.received_evidence
    # (ii) unverified 그대로 실림 + grounding_ok/compliant 안 내림
    pa = result["publication_audit"]
    assert any(f["type"] == "unverified" for f in pa["flags"])
    assert pa["grounding_ok"] is True and pa["compliant"] is True
