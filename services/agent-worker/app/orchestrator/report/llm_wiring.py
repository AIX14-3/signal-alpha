from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient
from app.observability.langsmith import maybe_trace


@dataclass(frozen=True)
class ReportLlmConfig:
    client: Any
    model: str
    timeout_seconds: float


def build_report_llm_config(settings: Any) -> ReportLlmConfig | None:
    if not bool(getattr(settings, "report_use_llm", False)):
        return None

    model = str(getattr(settings, "report_llm_model", "") or "").strip()
    if not model:
        return None

    provider = str(getattr(settings, "report_llm_provider", "gemini") or "gemini").strip().lower()
    timeout_seconds = float(getattr(settings, "report_llm_timeout_seconds", 20.0) or 20.0)

    if provider == "gemini":
        api_key = str(getattr(settings, "gemini_api_key", "") or "").strip()
        if not api_key:
            return None
        base_url = str(getattr(settings, "gemini_base_url", "") or "").strip()
        client = GeminiGenerateContentClient(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )
    elif provider == "openai":
        api_key = str(getattr(settings, "openai_api_key", "") or "").strip()
        if not api_key:
            return None
        base_url = str(getattr(settings, "openai_base_url", "") or "").strip()
        client = OpenAiChatClient(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )
    else:
        return None

    return ReportLlmConfig(
        client=maybe_trace(client, name="report_llm"),
        model=model,
        timeout_seconds=timeout_seconds,
    )
