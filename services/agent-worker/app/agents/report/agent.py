"""Report source agent (Tier A): RAG over ``report_chunks`` + LLM reinterpretation.

Implements the ``SourceAnalysisAgent`` contract. **Invariant:** this agent NEVER
computes the persisted score/direction — those are owned by the deterministic
``ReportAnalyzeTaskHandler`` and passed in via ``input_data.context``; the agent echoes
them verbatim. The LLM only produces reinterpretation text + grounded evidence, surfaced
through ``method_detail["report_rag"]`` (a purely additive side-car; the caller keeps
``method_score``/``method_signal`` and every existing feature key untouched).

Guardrails: the prompt forbids buy/sell/target-price/confidence language, evidence
excerpts are length-capped and paraphrased (copyright), and the output is re-scanned with
the shared advice-forbidding patterns. On any missing dependency, empty retrieval, LLM
error, or guardrail rejection it degrades to a conservative rules fallback
(``needs_review=True``) rather than failing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.report.retriever import retrieve
from app.clients.embedding_client import EmbeddingError, GeminiEmbeddingClient
from app.clients.gemini_client import GeminiError, GeminiJsonClient
from app.collectors.report.parsers.valuation_llm import FORBIDDEN_ADVICE_PATTERNS

logger = logging.getLogger(__name__)

# 공유 FORBIDDEN_ADVICE_PATTERNS(valuation_llm)가 못 잡는데 이 에이전트의 프롬프트는 금지하는
# 표현들 — 상승여력/추천(단독)/upside. 공유 패턴을 건드리지 않고 여기서만 보강한다.
_EXTRA_ADVICE_PATTERNS = (
    re.compile(r"상승\s*여력"),
    re.compile(r"추천"),
    re.compile(r"\bupside\b", re.IGNORECASE),
)

PROMPT_VER = "report-rag-v1"
QUESTIONS = (
    "이 리포트의 핵심 투자 논거와 실적 전망은?",
    "밸류에이션 근거(멀티플/목표가 산정 방식)는?",
    "리스크 요인과 하방 시나리오는?",
)
_MAX_CONTEXT_CHUNKS = 8
_MAX_EXCERPT_CHARS = 200

_PROMPT_TEMPLATE = """You reinterpret Korean broker-report evidence for Signal Alpha.
Ground EVERY statement ONLY in the retrieved excerpts below; add no outside facts.

Return strict JSON only:
{{
  "summary": "2-3 neutral Korean sentences on the data direction and thesis",
  "risk_flags": ["short_snake_case", ...],
  "evidence": [
    {{"point": "one paraphrased finding (Korean)", "raw_document_id": <int>,
      "broker": "<string or null>", "publish_date": "<YYYY-MM-DD or null>"}}
  ],
  "needs_review": true | false
}}

Rules:
- Do NOT use buy/sell/hold/target-price/recommendation/upside language
  (매수/매도/보유/목표가/추천/상승여력).
- Do NOT invent or state a score, direction, or "confidence".
- Paraphrase; never copy long verbatim passages (copyright). Keep each point short.
- If the evidence is thin or conflicting, set needs_review=true.

Deterministic context (already decided elsewhere — do not restate as advice):
{quant}

Retrieved evidence (JSON lines):
{evidence}
"""


class ReportAnalysisAgent:
    source = "REPORT"

    def __init__(
        self,
        *,
        connection: Any,
        embedder: Any | None = None,
        llm: Any | None = None,
        retriever: Any = retrieve,
        top_k: int = 5,
    ) -> None:
        self._connection = connection
        self._embedder = embedder
        self._llm = llm
        self._retriever = retriever
        self._top_k = top_k

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        context = input_data.context or {}
        direction = str(context.get("direction") or "unknown")
        score = _as_float(context.get("source_score"))
        quant = context.get("report_quant") or {}

        embedder = self._embedder or _safe_build(GeminiEmbeddingClient)
        llm = self._llm or _safe_build(GeminiJsonClient)
        if embedder is None or llm is None or input_data.stock_id is None:
            return self._fallback(
                input_data, direction, score, quant,
                status="deps_unavailable", llm_error="embedder_or_llm_or_stock_id_unavailable",
            )

        try:
            chunks = await self._gather(input_data.stock_id, embedder)
        except Exception as exc:  # noqa: BLE001 — retrieval must never crash the analyze step
            logger.warning(
                "report_rag_retrieval_failed stock_id=%s error=%s", input_data.stock_id, exc
            )
            return self._fallback(
                input_data, direction, score, quant, status="retrieval_error", llm_error=str(exc)
            )
        if not chunks:
            return self._fallback(
                input_data, direction, score, quant,
                status="no_evidence", llm_error="no_report_chunks",
            )

        try:
            payload = await llm.generate_json(self._build_prompt(quant, chunks))
        except GeminiError as exc:
            logger.warning("report_rag_llm_failed stock_id=%s error=%s", input_data.stock_id, exc)
            return self._fallback(
                input_data, direction, score, quant, status="llm_error", llm_error=str(exc)
            )

        try:
            summary, risk_flags, evidence, needs_review = _validate(payload, chunks)
        except ValueError as exc:
            logger.warning(
                "report_rag_guardrail_rejected stock_id=%s error=%s", input_data.stock_id, exc
            )
            return self._fallback(
                input_data, direction, score, quant,
                status="advice_language_rejected", llm_error="advice_language_rejected",
            )

        return SourceAgentOutput(
            source="REPORT",
            stock_code=input_data.stock_code,
            direction=direction,  # echo — never computed here (invariant)
            score=score,  # echo
            summary=summary,
            risk_flags=risk_flags,
            method_detail={"report_rag": {"status": "ok", "evidence": evidence}},
            needs_review=needs_review,
            data_status="ok",
            analysis_source="llm",
            llm_model=getattr(llm, "model", None),
            prompt_ver=PROMPT_VER,
        )

    async def _gather(self, stock_id: int, embedder: Any) -> list[dict[str, Any]]:
        """Multi-query RAG: union of Top-K per question, deduped, capped."""
        seen: set[tuple[Any, str]] = set()
        collected: list[dict[str, Any]] = []
        for question in QUESTIONS:
            rows = await self._retriever(
                self._connection,
                stock_id=stock_id,
                query=question,
                embedder=embedder,
                top_k=self._top_k,
            )
            for row in rows:
                text = str(row.get("chunk_text") or "").strip()
                key = (row.get("raw_document_id"), text)
                if not text or key in seen:
                    continue
                seen.add(key)
                collected.append(row)
                if len(collected) >= _MAX_CONTEXT_CHUNKS:
                    return collected
        return collected

    def _build_prompt(self, quant: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
        evidence_lines = [
            json.dumps(
                {
                    "raw_document_id": row.get("raw_document_id"),
                    "broker": row.get("broker"),
                    "publish_date": _date_str(row.get("publish_date")),
                    "excerpt": str(row.get("chunk_text") or "").strip()[:_MAX_EXCERPT_CHARS],
                },
                ensure_ascii=False,
            )
            for row in chunks
        ]
        quant_json = json.dumps(quant, ensure_ascii=False, default=str)
        return _PROMPT_TEMPLATE.format(quant=quant_json, evidence="\n".join(evidence_lines))

    def _fallback(
        self,
        input_data: SourceAgentInput,
        direction: str,
        score: float,
        quant: dict[str, Any],
        *,
        status: str,
        llm_error: str,
    ) -> SourceAgentOutput:
        return SourceAgentOutput(
            source="REPORT",
            stock_code=input_data.stock_code,
            direction=direction,  # echo
            score=score,  # echo
            summary=_fallback_summary(quant, direction),
            risk_flags=[],
            method_detail={"report_rag": {"status": status, "evidence": []}},
            needs_review=True,
            data_status="ok",
            analysis_source="rules",
            llm_model=None,
            prompt_ver=PROMPT_VER,
            llm_error=llm_error,
        )


def _safe_build(factory: Any) -> Any | None:
    """Construct a Gemini client, returning None if its API key is unset."""
    try:
        return factory()
    except (EmbeddingError, GeminiError):
        return None


def _validate(
    payload: Any, chunks: list[dict[str, Any]]
) -> tuple[str, list[str], list[dict[str, Any]], bool]:
    if not isinstance(payload, dict):
        raise ValueError("RAG payload must be a JSON object.")
    summary = str(payload.get("summary") or "").strip()
    risk_flags = [str(flag).strip() for flag in (payload.get("risk_flags") or []) if str(flag).strip()]
    evidence = _safe_evidence(payload.get("evidence"), _provenance(chunks))
    needs_review = bool(payload.get("needs_review", False)) or not summary
    _reject_advice(summary, risk_flags, evidence)
    return summary, risk_flags, evidence, needs_review


def _provenance(chunks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Trusted provenance from the ACTUALLY retrieved rows, keyed by raw_document_id.

    Evidence citations are grounded against this map (never the LLM's self-reported
    ids/broker/date), so a hallucinated ``raw_document_id`` — or advice text smuggled
    into a broker/date field — can never be persisted as if it were a real citation.
    """
    prov: dict[str, dict[str, Any]] = {}
    for row in chunks:
        rid = row.get("raw_document_id")
        prov.setdefault(
            str(rid),
            {"raw_document_id": rid, "broker": row.get("broker"), "publish_date": _date_str(row.get("publish_date"))},
        )
    return prov


def _safe_evidence(items: Any, prov: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only short paraphrased points whose citation matches a retrieved chunk.

    Provenance (broker/publish_date/raw_document_id) is sourced from ``prov`` (the DB
    rows), NOT from the LLM. Items citing an unretrieved id are dropped as ungrounded.
    """
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        point = str(item.get("point") or "").strip()[:_MAX_EXCERPT_CHARS]
        meta = prov.get(str(item.get("raw_document_id")))
        if not point or meta is None:
            continue
        out.append({
            "point": point,
            "raw_document_id": meta["raw_document_id"],
            "broker": meta["broker"],
            "publish_date": meta["publish_date"],
        })
    return out


def _reject_advice(summary: str, risk_flags: list[str], evidence: list[dict[str, Any]]) -> None:
    parts = [summary, *risk_flags, *[str(item.get("point") or "") for item in evidence]]
    text = "\n".join(part for part in parts if part)
    for pattern in (*FORBIDDEN_ADVICE_PATTERNS, *_EXTRA_ADVICE_PATTERNS):
        if pattern.search(text):
            raise ValueError("RAG output contains investment advice language.")


def _fallback_summary(quant: dict[str, Any], direction: str) -> str:
    valuation = (quant or {}).get("valuation") or {}
    methodology = valuation.get("methodology") or "unknown"
    return (
        f"결정론 밸류에이션 기준 데이터 방향성은 '{direction}'이며 산정 방식은 {methodology}입니다. "
        "리포트 원문 RAG 재해석은 근거 부족/미가용으로 생략됐습니다(검토 필요)."
    )


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _date_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]
