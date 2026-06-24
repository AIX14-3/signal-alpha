"""LangSmith 관측 — architecture.mermaid의 "LangSmith 관측 · 디버깅" (점선: 관측만).

LLM 호출(소스별 DART LLM + 끝단 synthesis)을 trace로 감싸 LangSmith에 기록한다. **관측 전용**:
파이프라인 입출력·흐름·결과를 절대 바꾸지 않으며, 트레이싱 실패는 전부 삼켜 LLM 호출에 영향을
주지 않는다. 기본 off(`LANGSMITH_TRACING` 미설정)이고, 켜져 있어도 `langsmith` 미설치/키 부재면
원본 클라이언트를 그대로 반환(no-op).

사용: 클라이언트 생성부에서 ``client = maybe_trace(client, name="synthesis")`` 로 감싼다.
LLM 클라이언트 계약은 ``app.analyzers.dart.llm.LlmClient`` (async ``complete``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from functools import lru_cache
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# 같은 사유는 1회만 경고 — 켜놓고 조용히 죽는(키 오류·SDK 드리프트) 상황을 감지 가능하게 하되
# per-call 스팸은 막는다.
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("[langsmith] %s", message)


class _Recorder(Protocol):
    def start(self, *, name: str, model: str, prompt: str) -> Any: ...
    def end(self, handle: Any, *, output: str, latency_ms: float) -> None: ...
    def error(self, handle: Any, *, exc: Exception, latency_ms: float) -> None: ...


class TracedLlmClient:
    """LlmClient 래퍼 — complete 호출을 recorder로 관측. 출력은 그대로 통과시킨다."""

    def __init__(self, inner: Any, recorder: _Recorder, *, name: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._name = name

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        # 관측 I/O(LangSmith HTTP)는 워커 스레드로 오프로딩 — 이벤트 루프를 막지 않고
        # 핫패스(LLM 호출)에 지연을 더하지 않는다. 관측 실패는 삼키되 LLM 예외는 그대로 전파.
        handle = await _safe_to_thread(
            self._recorder.start, name=self._name, model=model, prompt=prompt
        )
        started = time.perf_counter()
        try:
            output = await self._inner.complete(
                prompt=prompt, model=model, timeout_seconds=timeout_seconds
            )
        except Exception as exc:
            await _safe_to_thread(
                self._recorder.error, handle, exc=exc, latency_ms=_elapsed_ms(started)
            )
            raise
        await _safe_to_thread(
            self._recorder.end, handle, output=output, latency_ms=_elapsed_ms(started)
        )
        return output


def maybe_trace(client: Any, *, name: str) -> Any:
    """관측이 켜져 있고 사용 가능하면 TracedLlmClient로 감싸고, 아니면 원본을 그대로 반환."""
    if not tracing_enabled():
        return client
    recorder = _build_langsmith_recorder()
    if recorder is None:
        return client
    return TracedLlmClient(client, recorder, name=name)


def tracing_enabled() -> bool:
    return str(os.getenv("LANGSMITH_TRACING") or "").strip().lower() in {"1", "true", "yes", "on"}


class LangSmithRecorder:
    """langsmith.Client로 run을 기록. 생성/기록 실패는 호출측(TracedLlmClient)이 삼킨다."""

    def __init__(self, *, project: str, api_key: str, endpoint: str | None = None) -> None:
        import langsmith  # lazy: 미설치면 ImportError → maybe_trace가 no-op 처리

        self._client = langsmith.Client(api_key=api_key, api_url=endpoint)
        self._project = project

    def start(self, *, name: str, model: str, prompt: str) -> Any:
        import uuid
        from datetime import datetime, timezone

        run_id = uuid.uuid4()
        self._client.create_run(
            name=name,
            run_type="llm",
            id=run_id,
            inputs={"model": model, "prompt_chars": len(prompt)},
            project_name=self._project,
            start_time=datetime.now(timezone.utc),
        )
        return run_id

    def end(self, handle: Any, *, output: str, latency_ms: float) -> None:
        from datetime import datetime, timezone

        if handle is None:
            return
        self._client.update_run(
            handle,
            outputs={"output_chars": len(output), "latency_ms": round(latency_ms, 1)},
            end_time=datetime.now(timezone.utc),
        )

    def error(self, handle: Any, *, exc: Exception, latency_ms: float) -> None:
        from datetime import datetime, timezone

        if handle is None:
            return
        self._client.update_run(
            handle,
            error=str(exc),
            outputs={"latency_ms": round(latency_ms, 1)},
            end_time=datetime.now(timezone.utc),
        )


@lru_cache(maxsize=4)
def _cached_recorder(project: str, endpoint: str | None, key_fingerprint: str) -> _Recorder | None:
    """설정(project/endpoint/키지문)당 recorder 1개만 생성·재사용 — langsmith.Client(백그라운드
    배치 스레드 동반)가 태스크마다 새로 만들어지지 않게 한다. 실패는 1회 경고 후 None 캐시.

    실제 API 키는 캐시 키에 넣지 않고(시크릿이 캐시에 상주하지 않게) 짧은 지문만 키로 써서
    키 교체 시 재생성되게 한다. 진짜 키는 호출 시점에 env에서 읽는다.
    """
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        return None
    try:
        return LangSmithRecorder(project=project, api_key=api_key, endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001 — langsmith 미설치/초기화 실패 → 관측 비활성(no-op)
        _warn_once(
            "langsmith_build",
            f"LangSmith 관측 초기화 실패(관측 비활성, 파이프라인 영향 없음): {exc}",
        )
        return None


def _build_langsmith_recorder() -> _Recorder | None:
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        _warn_once(
            "langsmith_no_key",
            "LANGSMITH_TRACING이 켜졌지만 LANGSMITH_API_KEY가 없어 관측이 비활성화됩니다.",
        )
        return None
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return _cached_recorder(
        os.getenv("LANGSMITH_PROJECT") or "signal-alpha",
        os.getenv("LANGSMITH_ENDPOINT") or None,
        fingerprint,
    )


async def _safe_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
    """recorder 호출을 워커 스레드에서 실행하고 실패는 삼킨다(관측이 흐름·지연에 영향 X)."""
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 관측 실패는 LLM 호출에 영향 주지 않는다
        _warn_once("langsmith_record", f"LangSmith 기록 실패(관측만 영향): {exc}")
        return None


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
