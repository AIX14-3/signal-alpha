"""Shared async Gemini embedding client for episodic memory.

Stage 0 (agent embeddings infra): episodic memory (``signal_episodes``) stores a
``vector(768)``. This client turns
text into those 768-dim vectors via Gemini's ``embedContent`` /
``batchEmbedContents`` endpoints. It mirrors ``gemini_client.py`` exactly:

- async (a batch can overlap I/O via ``asyncio``),
- bounded retries with exponential backoff on transient HTTP errors (429/5xx),
- dependency-free transport (``urllib`` in a thread, same as the JSON client),
- no hardcoded key — ``GEMINI_API_KEY`` from the environment, same as Gemini.

Auth / endpoint are identical to ``gemini_client``: ``GEMINI_API_KEY`` against
``generativelanguage.googleapis.com``. Model is ``EMBEDDING_MODEL``
(default ``text-embedding-004``, natively 768-dim); when a Matryoshka model such
as ``gemini-embedding-001`` is selected we pass ``outputDimensionality`` so the
returned vector is still ``EMBEDDING_DIM`` (default 768). Every returned vector's
length is asserted to equal ``EMBEDDING_DIM`` so a silent dimension drift can
never reach the ``vector(768)`` column.

Boundary: infra layer. This does NOT wire embeddings into any agent (that is
Stage 1/2) — it only produces vectors. Collectors/analyzers must not import it.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?key={key}"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Models that accept Matryoshka truncation via outputDimensionality. text-embedding-004
# is natively 768 and does not need (nor universally accept) the field, so we only send
# it for the gemini-embedding family per the 768-dim contract.
_TRUNCATABLE_PREFIX = "gemini-embedding"


class EmbeddingError(RuntimeError):
    """Embedding call failed after retries, or returned an unusable body."""


class GeminiEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        max_attempts: int = 3,
        timeout: float = 60.0,
        retry_backoff: float = 1.5,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise EmbeddingError("GEMINI_API_KEY is required for embedding.")
        self._model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-004")
        self._dim = dim if dim is not None else int(os.getenv("EMBEDDING_DIM", "768"))
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._retry_backoff = retry_backoff

    @property
    def model(self) -> str:
        """Model id, for recording embedding provenance."""
        return self._model

    @property
    def dim(self) -> int:
        """Expected embedding dimensionality (matches the ``vector(N)`` column)."""
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Return the ``dim``-length embedding for ``text``.

        Retries transient HTTP failures with exponential backoff; raises
        ``EmbeddingError`` once attempts are exhausted or the body is unusable.
        """
        payload = {
            "model": f"models/{self._model}",
            "content": {"parts": [{"text": text}]},
        }
        if self._wants_output_dim():
            payload["outputDimensionality"] = self._dim
        return await self._request("embedContent", payload, self._extract_one)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one ``dim``-length embedding per input text (order preserved)."""
        if not texts:
            return []
        requests = []
        for text in texts:
            req: dict[str, Any] = {
                "model": f"models/{self._model}",
                "content": {"parts": [{"text": text}]},
            }
            if self._wants_output_dim():
                req["outputDimensionality"] = self._dim
            requests.append(req)
        return await self._request(
            "batchEmbedContents",
            {"requests": requests},
            lambda body: self._extract_many(body, expected=len(texts)),
        )

    def _wants_output_dim(self) -> bool:
        return self._model.startswith(_TRUNCATABLE_PREFIX)

    async def _request(
        self, method: str, payload: dict[str, Any], extract: Callable[[Any], Any]
    ) -> Any:
        """Call ``method`` and parse its body, retrying transient failures.

        Extraction runs inside the retry loop so a transient empty/partial body
        (marked ``retryable``) is retried too — matching ``gemini_client``, which
        retries empty-candidate responses.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                body = await asyncio.to_thread(self._call, method, payload)
                return extract(body)
            except EmbeddingError as exc:
                last_error = exc
                if not getattr(exc, "retryable", False) or attempt == self._max_attempts - 1:
                    raise
            # Backoff between attempts; clock-free sleep is fine here (I/O layer).
            await asyncio.sleep(self._retry_backoff ** attempt)
        raise EmbeddingError(str(last_error))

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = _ENDPOINT.format(model=self._model, method=method, key=self._api_key)
        req = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            err = EmbeddingError(f"Gemini embedding HTTP {exc.code}")
            err.retryable = exc.code in _RETRYABLE_STATUS  # type: ignore[attr-defined]
            raise err from exc
        except (URLError, TimeoutError) as exc:
            err = EmbeddingError(f"Gemini embedding transport error: {exc}")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        except json.JSONDecodeError as exc:
            err = EmbeddingError(f"Gemini embedding returned non-JSON body: {exc}")
            err.retryable = False  # type: ignore[attr-defined]
            raise err from exc

    def _extract_one(self, body: Any) -> list[float]:
        try:
            values = body["embedding"]["values"]
        except (KeyError, IndexError, TypeError) as exc:
            err = EmbeddingError("Gemini embedding response had no embedding values.")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        return self._validate(values)

    def _extract_many(self, body: Any, *, expected: int) -> list[list[float]]:
        try:
            embeddings = body["embeddings"]
        except (KeyError, TypeError) as exc:
            err = EmbeddingError("Gemini batch response had no embeddings array.")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        if not isinstance(embeddings, list) or len(embeddings) != expected:
            raise EmbeddingError(
                f"Gemini batch returned {len(embeddings) if isinstance(embeddings, list) else '?'} "
                f"embeddings for {expected} inputs."
            )
        out = []
        for item in embeddings:
            try:
                out.append(self._validate(item["values"]))
            except (KeyError, TypeError) as exc:
                raise EmbeddingError("Gemini batch item missing values.") from exc
        return out

    def _validate(self, values: Any) -> list[float]:
        if not isinstance(values, list) or len(values) != self._dim:
            got = len(values) if isinstance(values, list) else type(values).__name__
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self._dim}, got {got}."
            )
        return [float(v) for v in values]
