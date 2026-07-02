"""발행물 감사(감사 agent) 배선 v1-lite 게이트 검증.

핵심 안전 보장 = "감사가 off/미구성이면 발행 경로에 무영향(byte-동일 inert)". `_audit_publication` 의 skip 3경로
(비-LLM 서술 · 플래그 off · 키 없음)는 report/narrative 를 건드리기 전에 None 을 반환하므로, LLM·DB·dataclass
없이 그대로 검증한다. (감사 ON 경로는 라이브(run-pipeline)로 검증 — 감사 agent 자체는 tests/audit 8/8.)
"""

from __future__ import annotations

import asyncio

from app.synthesis.tasks import SynthesizeTaskHandler


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
