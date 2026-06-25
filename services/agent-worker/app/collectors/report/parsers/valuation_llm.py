from __future__ import annotations

import asyncio
import json
import re
from typing import Any

ALLOWED_METHODOLOGIES = {"PER", "PBR", "EV_EBITDA", "SOTP", "DCF", "mixed", "unknown"}
FORBIDDEN_ADVICE_PATTERNS = (
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s+price\b", re.IGNORECASE),
    re.compile(r"\brecommend(?:ation)?\b", re.IGNORECASE),
    re.compile(r"매수|매도|보유|투자\s*추천|목표가"),
)

VALUATION_ENRICHMENT_PROMPT = """
You classify broker-report valuation context for Signal Alpha.

Return strict JSON only:
{
  "methodology": "PER" | "PBR" | "EV_EBITDA" | "SOTP" | "DCF" | "mixed" | "unknown",
  "category_tag": "short_snake_case_tag_or_null",
  "rerating_thesis": "one neutral evidence-focused sentence or null",
  "needs_review": true | false
}

Rules:
- Do not create or change numeric values.
- Do not provide buy, sell, hold, target-price, upside, or investment recommendation language.
- Focus on data direction, evidence, source alignment, and review needs.
- If the text is insufficient, use null fields and needs_review=true.
""".strip()


def enrich_valuation_facts_with_llm(
    text: str,
    facts: dict[str, Any],
    *,
    llm_config: Any,
) -> dict[str, Any]:
    try:
        payload = _request_valuation_enrichment(text, facts, llm_config)
        return _merge_safe_payload(facts, payload)
    except Exception:
        return _fallback(facts)


def _request_valuation_enrichment(text: str, facts: dict[str, Any], llm_config: Any) -> dict[str, Any]:
    prompt = _build_prompt(text, facts)
    response_text = _run_complete(
        llm_config.client.complete(
            prompt=prompt,
            model=llm_config.model,
            timeout_seconds=llm_config.timeout_seconds,
        )
    )
    payload = json.loads(response_text)
    if not isinstance(payload, dict):
        raise ValueError("Valuation LLM response must be a JSON object.")
    return payload


def _merge_safe_payload(facts: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    methodology = str(payload.get("methodology") or facts.get("methodology") or "unknown").strip()
    if methodology not in ALLOWED_METHODOLOGIES:
        raise ValueError(f"Invalid valuation methodology: {methodology}")

    category_tag = _optional_short_slug(payload.get("category_tag"))
    rerating_thesis = _optional_short_text(payload.get("rerating_thesis"))
    _reject_advice([category_tag, rerating_thesis])

    enriched = dict(facts)
    enriched.update({
        "methodology": methodology,
        "category_tag": category_tag,
        "rerating_thesis": rerating_thesis,
        "extraction_source": "llm",
        "needs_review": bool(facts.get("needs_review")) or bool(payload.get("needs_review")),
    })
    return enriched


def _fallback(facts: dict[str, Any]) -> dict[str, Any]:
    fallback = dict(facts)
    fallback["extraction_source"] = "rules_fallback"
    fallback["needs_review"] = True
    return fallback


def _build_prompt(text: str, facts: dict[str, Any]) -> str:
    fact_snapshot = {
        "target_price": facts.get("target_price"),
        "forward_eps_est": facts.get("forward_eps_est"),
        "eps_fy": facts.get("eps_fy"),
        "methodology": facts.get("methodology"),
        "applied_multiple": facts.get("applied_multiple"),
        "implied_multiple": facts.get("implied_multiple"),
        "peer_group": facts.get("peer_group") or [],
    }
    return (
        f"{VALUATION_ENRICHMENT_PROMPT}\n\n"
        f"Existing deterministic facts, do not alter numbers:\n"
        f"{json.dumps(fact_snapshot, ensure_ascii=False)}\n\n"
        f"Report text excerpt:\n{text[:6000]}"
    )


def _optional_short_slug(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not text or text == "null":
        return None
    if not re.fullmatch(r"[a-z0-9_]{1,80}", text):
        raise ValueError("category_tag must be short snake_case text.")
    return text


def _optional_short_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    if len(text) > 500:
        raise ValueError("rerating_thesis is too long.")
    return text


def _reject_advice(values: list[str | None]) -> None:
    text = "\n".join(value for value in values if value)
    for pattern in FORBIDDEN_ADVICE_PATTERNS:
        if pattern.search(text):
            raise ValueError("Valuation LLM output contains investment advice language.")


def _run_complete(awaitable: Any) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return str(asyncio.run(awaitable))

    raise RuntimeError("Report valuation LLM enrichment cannot run inside an active event loop.")
