"""Async Anthropic (Claude) JSON client for the news digest step.

Mirrors ``GeminiJsonClient``'s contract — async + a strict JSON return — but
targets Claude via the official ``anthropic`` SDK. The news digest step feeds a
stock's relevance-filtered article candidates to Claude in a single call that
both selects the high-impact ones and writes a one-line neutral summary, so the
client just needs "prompt (+ optional JSON schema) -> parsed JSON".

Why the SDK (not urllib like ``gemini_client``): the Messages API needs the
``refusal`` stop-reason guard and structured outputs, both of which the SDK
models directly; ``openai`` is already a worker dependency, so a second official
SDK is consistent. Retries on 429/5xx are handled inside the SDK (``max_retries``),
so — unlike the Gemini client — there is no hand-rolled backoff loop here.

Boundary: enrichment/digest layer only. Collectors never call an LLM; analyzers
read cached features. Digest is display-only and never touches scoring.
"""
from __future__ import annotations

import json
import os
from typing import Any

_DEFAULT_MODEL = "claude-sonnet-5"


class AnthropicError(RuntimeError):
    """Claude call failed after retries, was refused, or returned unusable JSON."""


class AnthropicJsonClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        timeout: float = 20.0,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        # An injected client (tests) bypasses the key requirement; a real client
        # needs a key so we fail fast rather than at first call.
        if client is None and not self._api_key:
            raise AnthropicError("ANTHROPIC_API_KEY is required for the news digest LLM.")
        self._model = model or os.getenv("NEWS_LLM_MODEL", _DEFAULT_MODEL)
        self._max_tokens = max_tokens
        self._client = client if client is not None else self._build_client(timeout, max_retries)

    def _build_client(self, timeout: float, max_retries: int) -> Any:
        # Imported lazily so importing this module doesn't hard-require `anthropic`
        # (the digest step is gated by NEWS_LLM_ENABLED; workers without it installed
        # or configured should still import cleanly).
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=self._api_key, timeout=timeout, max_retries=max_retries)

    @property
    def model(self) -> str:
        """Model id, for recording LLM provenance (e.g. stock_news_digest.model)."""
        return self._model

    async def generate_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
    ) -> Any:
        """Return the parsed JSON Claude emitted for ``prompt``.

        When ``schema`` is given, structured outputs constrain the response to
        that JSON schema. Raises ``AnthropicError`` on a safety refusal, an SDK
        error (after the SDK's own 429/5xx retries), or an unparseable body.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # Short structured extraction — no thinking needed; keeps cost/latency down.
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:  # SDK APIError subclasses; retries already exhausted.
            raise AnthropicError(f"Claude call failed: {exc}") from exc

        # A refusal is HTTP 200 with empty/partial content — guard before reading it.
        if getattr(response, "stop_reason", None) == "refusal":
            raise AnthropicError("Claude refused the request (safety classifier).")

        text = _first_text(response)
        try:
            return _loads_first_json(text)
        except ValueError as exc:
            raise AnthropicError(f"Claude did not return valid JSON: {exc}") from exc


def _first_text(response: Any) -> str:
    """Concatenate the text blocks of a Messages response (skips thinking/tool blocks)."""
    parts = [
        block.text
        for block in getattr(response, "content", []) or []
        if getattr(block, "type", None) == "text"
    ]
    if not parts:
        raise AnthropicError("Claude response had no text content.")
    return "".join(parts)


def _loads_first_json(text: str) -> Any:
    """Parse the first JSON value in ``text``, tolerating code fences / trailing data.

    Structured outputs already guarantee valid JSON, but this stays tolerant of a
    stray ```json fence or trailing prose so a well-formed object isn't discarded
    by strict ``json.loads`` ("Extra data"). Raises ``ValueError`` when no JSON
    value starts the (fence-stripped) text.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    obj, _ = json.JSONDecoder().raw_decode(cleaned)
    return obj
