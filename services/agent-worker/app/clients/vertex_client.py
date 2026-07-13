"""Vertex AI JSON 클라이언트 — GCP 결제계좌로 직접 과금되는 Gemini 경로.

## 왜 AI Studio 대신 Vertex 인가
``GeminiJsonClient`` 가 쓰는 ``generativelanguage.googleapis.com`` (AI Studio) 는 **AI Studio
선불 크레딧**으로 과금된다 — GCP 결제계좌와 **별개 주머니**다. 그래서 GCP에 카드가 연결돼
있어도 크레딧이 마르면 HTTP 429("prepayment credits are depleted")로 그냥 멈춘다(실제로 겪음).

Vertex(``aiplatform.googleapis.com``)는 ADC 인증 + GCP 결제계좌 직결(``trafficType: ON_DEMAND``).
API 키가 아예 필요 없다.

## 이 클라이언트가 GeminiJsonClient 의 버그 3개를 고친다
실측에서 gemini-2.5-flash 가 42콜 중 10콜(24%) 스키마 위반을 냈고, PATENT 는 재시도해도 계속
실패했다. 원인은 모델이 멍청해서가 아니라 **클라이언트가 응답을 검증하지 않아서**였다:

1. **``finishReason`` 미확인** — 응답이 ``MAX_TOKENS`` 로 잘려도 그냥 파싱을 시도했다.
   잘린 JSON 은 파싱은 되면서 필수 키가 없는 상태가 될 수 있다 → "missing 'scores' list".
2. **``maxOutputTokens`` 미설정** — 기본값이 작아 큰 코호트 응답이 잘렸다.
3. **thinking 미제어** — 2.5 계열은 thinking 이 **기본 활성**이고 출력 예산을 먹는다
   (실측 ``thoughtsTokenCount: 21`` — 5토큰 응답에 thinking 이 21토큰). 구조화 JSON 추출에는
   thinking 이 필요 없으므로 끈다.

``usageMetadata`` 로 **정확한 토큰 수**를 돌려주므로 비용 측정도 근사가 아니라 실측이다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class VertexError(RuntimeError):
    pass


def _access_token() -> str:
    """ADC 액세스 토큰. gcloud 는 Windows 에서 .cmd 라 shell=True 가 필요하다."""
    result = subprocess.run(
        "gcloud auth application-default print-access-token",
        shell=True, capture_output=True, text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise VertexError(
            "ADC 토큰을 얻지 못했다. `gcloud auth application-default login` 을 먼저 실행할 것. "
            f"stderr={result.stderr.strip()[:200]}"
        )
    return token


class VertexJsonClient:
    """``llm_scorer.JsonLlm`` 프로토콜 구현 (``.model`` + ``async generate_json``)."""

    def __init__(
        self,
        *,
        model: str | None = None,
        project: str | None = None,
        location: str = "global",
        temperature: float = 0.0,
        max_output_tokens: int = 16000,
        thinking_budget: int = 0,
        max_attempts: int = 6,
        timeout: float = 120.0,
    ) -> None:
        self._model = model or os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
        self._project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        if not self._project:
            raise VertexError("GOOGLE_CLOUD_PROJECT (또는 GCP_PROJECT) 가 필요하다.")
        self._location = location
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._thinking_budget = thinking_budget
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._token: str | None = None
        # 마지막 호출의 실측 토큰(비용 측정용). 호출자가 읽어 간다.
        self.last_usage: dict[str, int] = {}

    @property
    def model(self) -> str:
        return self._model

    def _url(self) -> str:
        host = (
            "aiplatform.googleapis.com"
            if self._location == "global"
            else f"{self._location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{self._project}/locations/{self._location}"
            f"/publishers/google/models/{self._model}:generateContent"
        )

    async def generate_json(self, prompt: str) -> Any:
        """429 는 **크레딧 고갈이 아니라 공유 용량 스로틀**(Resource exhausted)이다 — 지수 백오프로
        넘긴다. AI Studio 의 'prepayment credits depleted' 와 달리 기다리면 풀린다."""
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await asyncio.to_thread(self._call, prompt)
            except VertexError as exc:
                last = exc
                if not getattr(exc, "retryable", False) or attempt == self._max_attempts - 1:
                    raise
            await asyncio.sleep(min(60.0, 4.0 * (2 ** attempt)))  # 4,8,16,32,60s
        raise VertexError(str(last))

    def _call(self, prompt: str) -> Any:
        if self._token is None:
            self._token = _access_token()
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "responseMimeType": "application/json",
                # 잘림 방지 — 코호트 응답은 길다.
                "maxOutputTokens": self._max_output_tokens,
                # 구조화 추출에 thinking 은 불필요하고, 출력 예산만 먹는다.
                "thinkingConfig": {"thinkingBudget": self._thinking_budget},
            },
        }
        req = Request(
            self._url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 401:  # 토큰 만료 → 다음 시도에 재발급
                self._token = None
            err = VertexError(f"Vertex HTTP {exc.code}: {detail}")
            err.retryable = exc.code in _RETRYABLE_STATUS or exc.code == 401  # type: ignore[attr-defined]
            raise err from exc
        except (URLError, TimeoutError) as exc:
            err = VertexError(f"Vertex transport error: {exc}")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc

        usage = payload.get("usageMetadata") or {}
        self.last_usage = {
            "input_tokens": int(usage.get("promptTokenCount") or 0),
            "output_tokens": int(usage.get("candidatesTokenCount") or 0),
            "thought_tokens": int(usage.get("thoughtsTokenCount") or 0),
        }

        candidates = payload.get("candidates") or []
        if not candidates:
            err = VertexError(f"Vertex returned no candidates (safety block?): {payload}")
            err.retryable = True  # type: ignore[attr-defined]
            raise err

        candidate = candidates[0]
        finish = candidate.get("finishReason")
        # ⚠️ 여기가 GeminiJsonClient 에 없던 검증이다. 잘린 응답을 조용히 파싱하면
        # "필수 키 없음"으로 나타나 **모델이 계약을 어긴 것처럼 보인다**(실제로 그렇게 오독했다).
        if finish == "MAX_TOKENS":
            err = VertexError(
                f"Vertex response truncated (finishReason=MAX_TOKENS, "
                f"output={self.last_usage['output_tokens']}, thoughts={self.last_usage['thought_tokens']}). "
                f"maxOutputTokens 를 올리거나 프롬프트를 줄여라."
            )
            err.retryable = False  # type: ignore[attr-defined]
            raise err
        if finish not in (None, "STOP"):
            err = VertexError(f"Vertex finishReason={finish}")
            err.retryable = finish in ("RECITATION",)  # type: ignore[attr-defined]
            raise err

        try:
            text = candidate["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            err = VertexError("Vertex response had no text part.")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc

        return _loads_first_json(text)


class VertexEmbeddingClient:
    """Vertex 임베딩 — ``GeminiEmbeddingClient`` 와 같은 계약(``.model`` + ``async embed``).

    임베딩도 AI Studio 경로면 크레딧이 마르는 순간 같이 죽는다(실제로 채점은 Vertex 로 넘겼는데
    임베딩만 AI Studio 라 429 로 터졌다). 에피소드 메모리를 GCP 결제로 돌리려면 이쪽이 필요하다.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        project: str | None = None,
        location: str = "us-central1",  # 임베딩은 global 미지원 → 리전 지정
        dim: int = 768,
        timeout: float = 60.0,
        max_attempts: int = 6,
    ) -> None:
        self._model = model or os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
        self._project = project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
        if not self._project:
            raise VertexError("GOOGLE_CLOUD_PROJECT 가 필요하다.")
        self._location = location
        self._dim = dim
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._token: str | None = None

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float]:
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await asyncio.to_thread(self._call, text)
            except VertexError as exc:
                last = exc
                if not getattr(exc, "retryable", False) or attempt == self._max_attempts - 1:
                    raise
            await asyncio.sleep(min(60.0, 4.0 * (2 ** attempt)))
        raise VertexError(str(last))

    def _call(self, text: str) -> list[float]:
        if self._token is None:
            self._token = _access_token()
        url = (
            f"https://{self._location}-aiplatform.googleapis.com/v1/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{self._model}:predict"
        )
        body = {
            "instances": [{"content": text}],
            "parameters": {"outputDimensionality": self._dim},
        }
        req = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 401:
                self._token = None
            err = VertexError(f"Vertex embed HTTP {exc.code}: {detail}")
            err.retryable = exc.code in _RETRYABLE_STATUS or exc.code == 401  # type: ignore[attr-defined]
            raise err from exc
        except (URLError, TimeoutError) as exc:
            err = VertexError(f"Vertex embed transport error: {exc}")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc

        try:
            values = payload["predictions"][0]["embeddings"]["values"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VertexError(f"Vertex embed unexpected shape: {str(payload)[:200]}") from exc
        # 차원 드리프트는 조용히 넘기면 pgvector INSERT 에서 터진다 — 여기서 잡는다.
        if len(values) != self._dim:
            raise VertexError(f"Vertex embed dim {len(values)} != expected {self._dim}")
        return [float(v) for v in values]


def _loads_first_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned)
    except ValueError as exc:
        err = VertexError(f"Vertex did not return valid JSON: {exc}. head={cleaned[:120]!r}")
        err.retryable = False  # type: ignore[attr-defined]
        raise err from exc
    return obj
