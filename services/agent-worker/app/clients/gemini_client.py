"""Shared async Gemini JSON client for enrichment tools.

Enrichment (e.g. patent significance extraction) calls Gemini once per item over
potentially hundreds of items, so unlike the one-shot keyword-generator helper it
needs: async (so a batch can overlap I/O via ``asyncio``), bounded retries on
transient HTTP errors, and a strict JSON contract (``responseMimeType`` = JSON +
``json.loads``). The transport stays dependency-free (urllib in a thread) to match
the rest of ``app/clients``.

Boundary: this lives in the enrichment/keyword-generator layer. Collectors and
analyzers must NOT import it — collectors never call an LLM, and analyzers only
read the cached features an enrichment tool produced.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    """Gemini call failed after retries, or returned an unusable body."""


class GeminiJsonClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_attempts: int = 3,
        timeout: float = 60.0,
        retry_backoff: float = 1.5,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not self._api_key:
            raise GeminiError("GEMINI_API_KEY is required for enrichment.")
        self._model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self._temperature = temperature
        self._max_attempts = max(1, max_attempts)
        self._timeout = timeout
        self._retry_backoff = retry_backoff

    @property
    def model(self) -> str:
        """Model id, for recording LLM provenance (e.g. agent_results.llm_model)."""
        return self._model

    async def generate_json(self, prompt: str) -> Any:
        """Return the parsed JSON Gemini emitted for ``prompt``.

        Retries transient HTTP failures with exponential backoff; raises
        ``GeminiError`` once attempts are exhausted or the body is not JSON.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await asyncio.to_thread(self._call, prompt)
            except GeminiError as exc:
                last_error = exc
                if not getattr(exc, "retryable", False) or attempt == self._max_attempts - 1:
                    raise
            # Backoff between attempts; clock-free sleep is fine here (I/O layer).
            await asyncio.sleep(self._retry_backoff ** attempt)
        raise GeminiError(str(last_error))

    def _call(self, prompt: str) -> Any:
        url = _ENDPOINT.format(model=self._model, key=self._api_key)
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "responseMimeType": "application/json",
            },
        }
        req = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            err = GeminiError(f"Gemini HTTP {exc.code}")
            err.retryable = exc.code in _RETRYABLE_STATUS  # type: ignore[attr-defined]
            raise err from exc
        except (URLError, TimeoutError) as exc:
            err = GeminiError(f"Gemini transport error: {exc}")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc

        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            # Empty candidates often means a safety block or quota truncation — retry.
            err = GeminiError("Gemini response had no candidate text.")
            err.retryable = True  # type: ignore[attr-defined]
            raise err from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            err = GeminiError(f"Gemini did not return valid JSON: {exc}")
            err.retryable = False  # type: ignore[attr-defined]
            raise err from exc
