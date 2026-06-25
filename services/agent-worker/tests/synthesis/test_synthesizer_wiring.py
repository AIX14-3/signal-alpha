"""SYNTHESIS_* env + api 키 → LLM 종합기 배선(provider 선택/게이팅).

MVP에서 '키를 넣으면 끝단 종합이 LLM 경로로 동작'하는 계약을 잠근다. 실제 API 호출은
하지 않는다 — _build_synthesizer가 올바른 클라이언트를 고르는지/미설정 시 결정론 폴백
(None)인지만 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient
from app.synthesis.tasks import _build_synthesizer, synthesis_llm_enabled


def _settings(*, gemini_key=None, openai_key=None):
    return SimpleNamespace(
        gemini_api_key=gemini_key,
        gemini_base_url=None,
        openai_api_key=openai_key,
        openai_base_url=None,
    )


def _enable_gemini(monkeypatch, model="gemini-2.0-flash"):
    monkeypatch.setenv("SYNTHESIS_USE_LLM", "true")
    monkeypatch.setenv("SYNTHESIS_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("SYNTHESIS_LLM_MODEL", model)


def test_disabled_when_toggle_off(monkeypatch):
    monkeypatch.delenv("SYNTHESIS_USE_LLM", raising=False)
    monkeypatch.setenv("SYNTHESIS_LLM_MODEL", "gemini-2.0-flash")
    assert synthesis_llm_enabled(_settings(gemini_key="k")) is False
    assert _build_synthesizer(_settings(gemini_key="k")) is None


def test_disabled_when_model_missing(monkeypatch):
    monkeypatch.setenv("SYNTHESIS_USE_LLM", "true")
    monkeypatch.setenv("SYNTHESIS_LLM_PROVIDER", "gemini")
    monkeypatch.delenv("SYNTHESIS_LLM_MODEL", raising=False)
    assert synthesis_llm_enabled(_settings(gemini_key="k")) is False


def test_disabled_when_key_missing(monkeypatch):
    _enable_gemini(monkeypatch)
    assert synthesis_llm_enabled(_settings(gemini_key=None)) is False
    assert _build_synthesizer(_settings(gemini_key=None)) is None


def test_gemini_provider_builds_gemini_client(monkeypatch):
    _enable_gemini(monkeypatch)
    settings = _settings(gemini_key="gkey")
    assert synthesis_llm_enabled(settings) is True
    synth = _build_synthesizer(settings)
    assert synth is not None
    assert synth.model == "gemini-2.0-flash"
    assert isinstance(synth._client, GeminiGenerateContentClient)


def test_openai_provider_builds_openai_client(monkeypatch):
    monkeypatch.setenv("SYNTHESIS_USE_LLM", "true")
    monkeypatch.setenv("SYNTHESIS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SYNTHESIS_LLM_MODEL", "gpt-4o-mini")
    settings = _settings(openai_key="okey")
    assert synthesis_llm_enabled(settings) is True
    synth = _build_synthesizer(settings)
    assert synth is not None
    assert isinstance(synth._client, OpenAiChatClient)


def test_unknown_provider_disabled(monkeypatch):
    monkeypatch.setenv("SYNTHESIS_USE_LLM", "true")
    monkeypatch.setenv("SYNTHESIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("SYNTHESIS_LLM_MODEL", "claude-x")
    # anthropic 미지원 → 비활성(결정론 폴백).
    assert synthesis_llm_enabled(_settings(gemini_key="k", openai_key="k")) is False
    assert _build_synthesizer(_settings(gemini_key="k", openai_key="k")) is None
