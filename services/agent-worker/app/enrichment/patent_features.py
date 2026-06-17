"""Extract structured significance features from a patent's title + abstract.

Pure logic (``build_prompt``/``validate_features``/``abstract_of``) is split from
the DB+Gemini orchestration (``PatentEnricher``) so the contract — clamping,
stage coercion, the JSON shape the analyzer relies on — is unit-tested without
any I/O.

Cached features (stored in ``patent_raw_details.llm_features``, see migration 019)
let the Patent analyzer weight filings by importance instead of just counting them.
A patent's ``application_no`` is immutable, so each patent is enriched exactly once.
"""
from __future__ import annotations

import json
from typing import Any

COMMERCIALIZATION_STAGES = {"research", "development", "near_market", "unknown"}
_ABSTRACT_KEYS = ("astrtCont", "abstract", "astrt_cont")
_MAX_ABSTRACT_CHARS = 2000
_MAX_RATIONALE_CHARS = 300


def abstract_of(extra_payload: Any) -> str:
    """Pull the Korean abstract KIPRIS stored in the raw payload (``astrtCont``)."""
    payload = _as_dict(extra_payload)
    for key in _ABSTRACT_KEYS:
        value = payload.get(key)
        if value:
            return str(value).strip()[:_MAX_ABSTRACT_CHARS]
    return ""


def build_prompt(title: str, abstract: str) -> str:
    return f"""
You are extracting structured R&D-significance features from ONE Korean patent for
a stock-signal pipeline. Return JSON only. No markdown. Judge only the patent's
technical/business importance — do NOT give investment advice or price views.

Patent:
- title: {title or "(제목 없음)"}
- abstract: {abstract or "(초록 없음)"}

Fields (all required):
- significance: 0.0-1.0. How impactful this invention is to the applicant's core
  business and competitiveness. Routine/defensive filing → low; platform/core
  technology → high.
- core_business_relevance: 0.0-1.0. How central the technology is to the company's
  main business (vs peripheral/unrelated).
- novelty: 0.0-1.0. How novel vs incremental the invention reads.
- commercialization_stage: one of "research", "development", "near_market",
  "unknown" — how close to a shippable product the abstract implies.
- rationale: <=300 Korean characters grounding the scores in the title/abstract.

If the abstract is empty or uninformative, return conservative low scores and
commercialization_stage "unknown".

JSON shape:
{{"significance": 0.0, "core_business_relevance": 0.0, "novelty": 0.0,
  "commercialization_stage": "unknown", "rationale": "..."}}
""".strip()


def validate_features(raw: Any) -> dict[str, Any]:
    """Coerce a Gemini response into the cached-feature contract.

    Clamps scores to [0,1], forces a known commercialization stage, and bounds the
    rationale. Raises ``ValueError`` only when the response is not a JSON object —
    individual bad fields are repaired, never trusted blindly.
    """
    if not isinstance(raw, dict):
        raise ValueError("Gemini feature response must be a JSON object.")
    stage = str(raw.get("commercialization_stage") or "unknown").strip().lower()
    if stage not in COMMERCIALIZATION_STAGES:
        stage = "unknown"
    rationale = str(raw.get("rationale") or "").strip()[:_MAX_RATIONALE_CHARS]
    return {
        "significance": _clamp01(raw.get("significance")),
        "core_business_relevance": _clamp01(raw.get("core_business_relevance")),
        "novelty": _clamp01(raw.get("novelty")),
        "commercialization_stage": stage,
        "rationale": rationale,
    }


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class PatentEnricher:
    """Enrich pending patents: title+abstract → Gemini → cached features.

    ``repository`` must expose ``list_unenriched_patent_details(limit,
    raw_document_ids)`` and ``update_patent_llm_features(raw_document_id,
    features, status)``; ``client`` must expose async ``generate_json(prompt)``.
    Both are injected so this class is testable with fakes.

    ``raw_document_ids`` scopes the run to a specific set of patents (the
    queue-driven ENRICH_PATENT path passes the raw_ids a NORMALIZE_PATENT task
    just promoted); pass None for the global pending-batch sweep.
    """

    def __init__(
        self,
        repository: Any,
        client: Any,
        *,
        batch_limit: int = 200,
        raw_document_ids: list[int] | None = None,
    ) -> None:
        self._repository = repository
        self._client = client
        self._batch_limit = batch_limit
        self._raw_document_ids = raw_document_ids

    async def run(self) -> dict[str, int]:
        rows = await self._repository.list_unenriched_patent_details(
            limit=self._batch_limit, raw_document_ids=self._raw_document_ids
        )
        stats = {"total": len(rows), "success": 0, "failed": 0, "skipped": 0}
        for row in rows:
            await self._enrich_one(row, stats)
        return stats

    async def _enrich_one(self, row: Any, stats: dict[str, int]) -> None:
        raw_document_id = row["raw_document_id"]
        title = str(row.get("patent_title") or "").strip()
        abstract = abstract_of(row.get("extra_payload"))
        if not title and not abstract:
            await self._repository.update_patent_llm_features(
                raw_document_id=raw_document_id, features=None, status="skipped"
            )
            stats["skipped"] += 1
            return
        try:
            raw = await self._client.generate_json(build_prompt(title, abstract))
            features = validate_features(raw)
        except Exception:
            await self._repository.update_patent_llm_features(
                raw_document_id=raw_document_id, features=None, status="failed"
            )
            stats["failed"] += 1
            return
        await self._repository.update_patent_llm_features(
            raw_document_id=raw_document_id, features=features, status="success"
        )
        stats["success"] += 1
