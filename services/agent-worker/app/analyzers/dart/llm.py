from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.analyzers.dart.financials import extract_dart_financial_metrics
from app.analyzers.dart.source_result import DartAnalysisResult

PROMPT_VERSION = "dart-llm-v1"
_ALLOWED_DIRECTIONS = {"positive", "negative", "neutral", "mixed"}
_PROHIBITED_ADVICE_REGEXES = [
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
]
_PROHIBITED_ADVICE_PATTERNS = [
    "buy",
    "sell",
    "hold",
    "target price",
    "매수",
    "매도",
    "보유",
    "목표가",
    "투자 추천",
]
_HIGH_IMPACT_EVENT_TYPES = {
    "annual_report",
    "business_report",
    "governance_report",
    "material_event",
    "quarter_report",
    "semiannual_report",
    "periodic_report",
    "major_disclosure",
    "major_shareholder_change",
    "supply_contract",
    "financing",
    "capital_increase",
    "correction",
}
_EVIDENCE_KEYWORDS = (
    "revenue",
    "sales",
    "operating profit",
    "net income",
    "profit",
    "loss",
    "cash flow",
    "debt",
    "liability",
    "equity",
    "매출",
    "영업이익",
    "당기순이익",
    "순이익",
    "손실",
    "현금흐름",
    "부채",
    "자본",
)


class DartLlmAnalysisError(ValueError):
    pass


class LlmClient(Protocol):
    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        pass


@dataclass(frozen=True)
class DartLlmAnalysis:
    direction: str
    score: float
    summary: str
    key_facts: list[str]
    risk_flags: list[str]
    needs_review: bool
    confidence: int


class DartLlmAnalyzer:
    def __init__(
        self,
        *,
        client: LlmClient,
        model: str,
        prompt_version: str = PROMPT_VERSION,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._client = client
        self.model = model
        self.prompt_version = prompt_version
        self._timeout_seconds = timeout_seconds

    async def analyze(
        self,
        *,
        events: list[dict[str, Any]],
        rule_result: DartAnalysisResult,
        stock_code: str,
    ) -> DartLlmAnalysis:
        prompt = _build_prompt(events=events, rule_result=rule_result, stock_code=stock_code)
        response_text = await self._client.complete(
            prompt=prompt,
            model=self.model,
            timeout_seconds=self._timeout_seconds,
        )
        return parse_dart_llm_response(response_text)


class OpenAiChatClient:
    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        return await asyncio.to_thread(
            self._complete_sync,
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    def _complete_sync(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        body = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "You analyze Korean DART disclosures and return strict JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise DartLlmAnalysisError(_http_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise DartLlmAnalysisError(f"LLM request failed: {exc}") from exc

        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise DartLlmAnalysisError("LLM response did not include message content.") from exc


class GeminiGenerateContentClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        return await asyncio.to_thread(
            self._complete_sync,
            prompt=prompt,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    def _complete_sync(self, *, prompt: str, model: str, timeout_seconds: float) -> str:
        body = json.dumps(
            {
                "contents": [
                    {
                        "parts": [
                            {
                                "text": prompt,
                            }
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0,
                    "response_mime_type": "application/json",
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        model_name = model if model.startswith("models/") else f"models/{model}"
        encoded_model = urllib.parse.quote(model_name, safe="/")
        encoded_key = urllib.parse.quote(self._api_key, safe="")
        request = urllib.request.Request(
            f"{self._base_url}/{encoded_model}:generateContent?key={encoded_key}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise DartLlmAnalysisError(_http_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise DartLlmAnalysisError(f"LLM request failed: {exc}") from exc

        try:
            parts = payload["candidates"][0]["content"]["parts"]
            return "".join(str(part.get("text") or "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise DartLlmAnalysisError("Gemini response did not include text content.") from exc


def parse_dart_llm_response(response_text: str) -> DartLlmAnalysis:
    payload = _loads_json_object(response_text)
    direction = _required_str(payload, "direction").lower()
    if direction not in _ALLOWED_DIRECTIONS:
        raise DartLlmAnalysisError(f"Invalid DART LLM direction: {direction}")

    summary = _required_str(payload, "summary")
    key_facts = _string_list(payload.get("key_facts"), "key_facts")
    _reject_investment_advice([summary, *key_facts])
    return DartLlmAnalysis(
        direction=direction,
        score=_bounded_unit_score(payload.get("score"), "score"),
        summary=summary,
        key_facts=key_facts,
        risk_flags=_string_list(payload.get("risk_flags"), "risk_flags"),
        needs_review=bool(payload.get("needs_review")),
        confidence=_bounded_int(payload.get("confidence"), "confidence", allow_fraction=True),
    )


def should_use_dart_llm(
    events: list[dict[str, Any]],
    *,
    high_impact_only: bool = True,
) -> bool:
    if not events:
        return False
    if not high_impact_only:
        return True

    for event in events:
        event_type = str(event.get("event_type") or "").strip()
        impact_level = str(event.get("impact_level") or "").strip()
        if impact_level in {"high", "medium"} and event_type in _HIGH_IMPACT_EVENT_TYPES:
            return True
    return False


def _build_prompt(
    *,
    events: list[dict[str, Any]],
    rule_result: DartAnalysisResult,
    stock_code: str,
) -> str:
    template = _prompt_template()
    payload = {
        "stock_code": stock_code,
        "rule_result": {
            "direction": rule_result.direction,
            "score": rule_result.score,
            "summary": rule_result.summary,
            "risk_flags": rule_result.risk_flags,
            "needs_review": rule_result.needs_review,
        },
        "events": [_event_payload(event) for event in events],
    }
    return template.replace("{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, default=str))


def _prompt_template() -> str:
    path = Path(__file__).resolve().parents[2] / "prompts" / "dart_analysis_v1.md"
    return path.read_text(encoding="utf-8")


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    evidence_text = str(event.get("evidence_text") or "")
    compact_text = _compact_evidence_text(evidence_text)
    return {
        "id": event.get("id"),
        "event_type": event.get("event_type"),
        "event_date": event.get("event_date"),
        "signal_direction": event.get("signal_direction"),
        "impact_level": event.get("impact_level"),
        "title": event.get("title"),
        "summary": event.get("summary"),
        "evidence_url": event.get("evidence_url") or event.get("source_url"),
        "financial_metrics": extract_dart_financial_metrics(evidence_text),
        "evidence_highlights": _evidence_highlights(compact_text),
        "evidence_text": compact_text[:2500],
        "needs_review": event.get("needs_review"),
    }


def _compact_evidence_text(text: str) -> str:
    if not text:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", text)
    without_entities = re.sub(r"&[a-zA-Z0-9#]+;", " ", without_tags)
    return re.sub(r"\s+", " ", without_entities).strip()


def _evidence_highlights(text: str, *, max_snippets: int = 6, window: int = 180) -> list[str]:
    if not text:
        return []
    lowered = text.lower()
    snippets: list[str] = []
    seen: set[str] = set()
    for keyword in _EVIDENCE_KEYWORDS:
        index = lowered.find(keyword.lower())
        if index < 0:
            continue
        start = max(0, index - window)
        end = min(len(text), index + len(keyword) + window)
        snippet = text[start:end].strip()
        if snippet and snippet not in seen:
            snippets.append(snippet)
            seen.add(snippet)
        if len(snippets) >= max_snippets:
            break
    if snippets:
        return snippets
    return [text[:500]]


def _loads_json_object(response_text: str) -> dict[str, Any]:
    text = response_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DartLlmAnalysisError("DART LLM response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise DartLlmAnalysisError("DART LLM response must be a JSON object.")
    return payload


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    detail = _http_error_detail(exc)
    if detail:
        return f"LLM request failed: HTTP {exc.code} {exc.reason}: {detail}"
    return f"LLM request failed: HTTP {exc.code} {exc.reason}"


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    if not body.strip():
        return ""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()[:500]
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return body.strip()[:500]

    parts = []
    for key in ("type", "status", "code", "message"):
        value = error.get(key)
        if value:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DartLlmAnalysisError(f"DART LLM response missing string field: {key}")
    return value.strip()


def _bounded_int(value: Any, key: str, *, allow_fraction: bool = False) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DartLlmAnalysisError(f"DART LLM response missing integer field: {key}") from exc
    if allow_fraction and 0 < number <= 1:
        number *= 100
    if number < 0 or number > 100:
        raise DartLlmAnalysisError(f"DART LLM field out of range: {key}")
    return int(round(number))


def _bounded_unit_score(value: Any, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DartLlmAnalysisError(f"DART LLM response missing numeric field: {key}") from exc
    if number < -1 or number > 1:
        raise DartLlmAnalysisError(f"DART LLM field out of range: {key}")
    return round(number, 3)


def _string_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DartLlmAnalysisError(f"DART LLM field must be a list: {key}")
    return [str(item).strip() for item in value if str(item).strip()]


def _reject_investment_advice(values: list[str]) -> None:
    text = " ".join(values)
    for pattern in _PROHIBITED_ADVICE_REGEXES:
        if pattern.search(text):
            raise DartLlmAnalysisError("DART LLM response contained investment advice language.")
    lowered = text.lower()
    for pattern in _PROHIBITED_ADVICE_PATTERNS:
        if pattern in {"buy", "sell", "hold", "target price"}:
            continue
        if pattern.lower() in lowered:
            raise DartLlmAnalysisError("DART LLM response contained investment advice language.")
