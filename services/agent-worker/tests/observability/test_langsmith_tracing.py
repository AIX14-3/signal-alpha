"""LangSmith 관측: 켜짐/꺼짐 게이트 + 관측이 LLM 호출 결과/흐름을 바꾸지 않음(실패도 삼킴)."""

from __future__ import annotations

import unittest

import pytest

from app.observability.langsmith import TracedLlmClient, maybe_trace, tracing_enabled


def test_tracing_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    assert tracing_enabled() is False


def test_maybe_trace_returns_same_client_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    sentinel = object()
    assert maybe_trace(sentinel, name="x") is sentinel


def test_maybe_trace_noop_when_enabled_but_no_recorder(monkeypatch) -> None:
    # 켜져 있어도 API 키 없거나 langsmith 미설치면 원본 클라이언트를 그대로 반환(no-op).
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    sentinel = object()
    assert maybe_trace(sentinel, name="x") is sentinel


class _FakeInner:
    def __init__(self, response="ok"):
        self.response = response
        self.calls = []

    async def complete(self, *, prompt, model, timeout_seconds):
        self.calls.append((prompt, model, timeout_seconds))
        return self.response


class _BoomInner:
    async def complete(self, *, prompt, model, timeout_seconds):
        raise RuntimeError("llm down")


class _RecordingRecorder:
    def __init__(self):
        self.events = []

    def start(self, *, name, model, prompt):
        self.events.append(("start", name, model))
        return "handle-1"

    def end(self, handle, *, output, latency_ms):
        self.events.append(("end", handle, output))

    def error(self, handle, *, exc, latency_ms):
        self.events.append(("error", handle, str(exc)))


class _BoomRecorder:
    def start(self, *, name, model, prompt):
        raise RuntimeError("recorder start failed")

    def end(self, handle, *, output, latency_ms):
        raise RuntimeError("recorder end failed")

    def error(self, handle, *, exc, latency_ms):
        raise RuntimeError("recorder error failed")


class TracedLlmClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_passes_through_output_and_records(self):
        inner = _FakeInner("RESULT")
        recorder = _RecordingRecorder()
        client = TracedLlmClient(inner, recorder, name="synthesis")

        out = await client.complete(prompt="p", model="m", timeout_seconds=5.0)

        self.assertEqual(out, "RESULT")  # 출력 불변
        self.assertEqual(inner.calls, [("p", "m", 5.0)])  # 그대로 위임
        self.assertEqual(recorder.events[0][0], "start")
        self.assertEqual(recorder.events[1], ("end", "handle-1", "RESULT"))

    async def test_recorder_failure_does_not_break_call(self):
        inner = _FakeInner("RESULT")
        client = TracedLlmClient(inner, _BoomRecorder(), name="synthesis")

        out = await client.complete(prompt="p", model="m", timeout_seconds=5.0)

        self.assertEqual(out, "RESULT")  # 관측 실패해도 LLM 결과는 그대로

    async def test_inner_error_is_recorded_and_reraised(self):
        recorder = _RecordingRecorder()
        client = TracedLlmClient(_BoomInner(), recorder, name="dart_llm")

        with pytest.raises(RuntimeError, match="llm down"):
            await client.complete(prompt="p", model="m", timeout_seconds=5.0)

        self.assertTrue(any(e[0] == "error" for e in recorder.events))


if __name__ == "__main__":
    unittest.main()
