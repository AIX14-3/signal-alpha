"""Report RAG 분석 Agent (SourceAnalysisAgent 구현).

main의 Source Agent 공통 계약(app/agents/base.py)을 따른다. DART agent와 동일하게
agent는 **순수 분석**만 한다 — DB·임베딩은 직접 다루지 않고, 주입된 retriever와
input_data.context(정량 메타)로만 동작한다.

흐름:
  1. 다중 질의(QUESTIONS)로 stock_id 격리 RAG 검색 → 청크 합집합·중복제거
  2. input_data.context["report_quant"] 의 정량 메타(목표주가/의견 분포 등) 결합
  3. LLM 종합(provider는 dart/llm.py의 LlmClient 재사용) → SourceAgentOutput
     - LLM 미설정/실패/근거 없음 → 보수적 fallback(needs_review, data_status 강등)

수집기는 증권사명으로 표본을 제한하지 않는다. 다만 네이버 금융 리서치 목록과 수집 기간,
종목 범위에 의존하므로 시장 전체 컨센서스로 단정하지 않는다. 컨센서스/conflict 신호는
점수보다 **근거 청크**를 신뢰하고 needs_review를 보수적으로. 한계를 method_detail.coverage에
기계가 읽을 수 있게 박는다(사용자 면책은 Phase 5 final_signals).
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput, SourceAgentStatus
from app.analyzers.dart.llm import LlmClient  # provider 추상화 재사용(분기 일원화)
from app.schemas.source_result import Direction

PROMPT_VERSION = "report-rag-v1"

# 다양한 관점을 회수하기 위한 다중 질의(가이드 §결정 5). 단일 질의보다 근거 폭이 넓다.
QUESTIONS = [
    "목표주가와 그 산정 근거",
    "최근 실적과 업황 전망",
    "투자 리스크 요인",
    "밸류에이션 판단",
]

# 수집 커버리지(기계가 읽는 메타). 사용자 면책은 final_signals disclaimer에서.
COVERAGE = {
    "firms": ["all_available_from_naver_research"],
    "note": "네이버 금융 리서치 목록과 수집 기간 기준 표본 — 시장 전체 컨센서스 아님",
}

_ALLOWED_DIRECTIONS: set[Direction] = {"positive", "neutral", "negative", "mixed", "unknown"}


class ReportLlmError(ValueError):
    pass


class ReportAnalysisAgent:
    source: str = "REPORT"

    def __init__(
        self,
        *,
        retriever: Any,
        llm_client: LlmClient | None = None,
        llm_model: str | None = None,
        top_k_per_query: int = 3,
        timeout_seconds: float = 20.0,
    ) -> None:
        # retriever: async callable (stock_id, query, top_k) -> list[dict]
        #   (analyzers/report/rag_retriever.ReportRagRetriever)
        self._retriever = retriever
        self._llm_client = llm_client
        self._llm_model = llm_model
        self._top_k = top_k_per_query
        self._timeout_seconds = timeout_seconds

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        stock_code = input_data.stock_code
        stock_id = input_data.stock_id
        quant = dict(input_data.context.get("report_quant") or {})

        if stock_id is None:
            return self._fallback(
                stock_code,
                summary="stock_id가 없어 RAG 검색을 수행할 수 없습니다.",
                chunks=[],
                quant=quant,
                data_status="failed",
                risk_flags=["evidence_required"],
            )

        # 1) 다중 질의 검색 → 합집합 → (raw_document_id, chunk_index) 기준 중복제거
        collected: list[dict[str, Any]] = []
        for question in QUESTIONS:
            collected += await self._retriever(
                int(stock_id), f"{stock_code} {question}", top_k=self._top_k
            )
        chunks = _dedupe_chunks(collected)

        if not chunks:
            return self._fallback(
                stock_code,
                summary="해당 종목의 임베딩된 리포트 근거를 찾지 못했습니다.",
                chunks=[],
                quant=quant,
                data_status="failed",
                risk_flags=["evidence_required"],
            )

        # 2) LLM 종합. 미설정이면 보수적 fallback(근거는 회수됐으나 종합 불가).
        if self._llm_client is None or not self._llm_model:
            return self._fallback(
                stock_code,
                summary=f"리포트 근거 {len(chunks)}건 회수(LLM 종합 미설정).",
                chunks=chunks,
                quant=quant,
                data_status="partial",
            )

        try:
            analysis = await self._synthesize(stock_code, chunks, quant)
        except Exception as exc:  # LLM 실패 → 근거 기반 보수적 fallback
            return self._fallback(
                stock_code,
                summary=f"리포트 근거 {len(chunks)}건 회수(LLM 종합 실패).",
                chunks=chunks,
                quant=quant,
                data_status="partial",
                llm_error=str(exc),
            )

        valuation_risk_flags = _valuation_risk_flags(quant)
        risk_flags = _merge_unique(analysis["risk_flags"], valuation_risk_flags)
        needs_review = bool(analysis["needs_review"]) or _valuation_needs_review(quant)
        data_status: SourceAgentStatus = "partial" if needs_review else "ok"
        return SourceAgentOutput(
            source="REPORT",
            stock_code=stock_code,
            direction=analysis["direction"],
            score=float(analysis["score"]),
            summary=analysis["summary"],
            risk_flags=risk_flags,
            method_detail={
                "coverage": COVERAGE,
                "key_rationale": analysis["key_rationale"],
                "report_quant": quant,
                "evidence_chunks": _evidence_refs(chunks),
                "llm_confidence": analysis["confidence"],
            },
            needs_review=needs_review,
            data_status=data_status,
            analysis_source="llm",
            llm_model=self._llm_model,
            prompt_ver=PROMPT_VERSION,
        )

    async def _synthesize(
        self, stock_code: str, chunks: list[dict[str, Any]], quant: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = _build_prompt(stock_code, chunks, quant)
        response_text = await self._llm_client.complete(  # type: ignore[union-attr]
            prompt=prompt,
            model=self._llm_model,  # type: ignore[arg-type]
            timeout_seconds=self._timeout_seconds,
        )
        return parse_report_llm_response(response_text)

    def _fallback(
        self,
        stock_code: str,
        *,
        summary: str,
        chunks: list[dict[str, Any]],
        quant: dict[str, Any],
        data_status: SourceAgentStatus,
        llm_error: str | None = None,
        risk_flags: list[str] | None = None,
    ) -> SourceAgentOutput:
        # 표본 작음 + 종합 불가 → 점수 중립, needs_review로 사람 검토 유도(결정 B).
        return SourceAgentOutput(
            source="REPORT",
            stock_code=stock_code,
            direction="unknown",
            score=50.0,
            summary=summary,
            risk_flags=_merge_unique(risk_flags or [], _valuation_risk_flags(quant)),
            method_detail={
                "coverage": COVERAGE,
                "report_quant": quant,
                "evidence_chunks": _evidence_refs(chunks),
            },
            needs_review=True,
            data_status=data_status,
            analysis_source="rules_fallback" if llm_error else "rules",
            llm_model=None,
            prompt_ver=PROMPT_VERSION,
            llm_error=llm_error,
        )


def _dedupe_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """(raw_document_id, chunk_index) 기준 중복 제거. 유사도 높은 것을 우선 보존."""
    ordered = sorted(chunks, key=lambda c: c.get("similarity", 0.0), reverse=True)
    seen: set[tuple[Any, Any]] = set()
    unique: list[dict[str, Any]] = []
    for chunk in ordered:
        key = (chunk.get("raw_document_id"), chunk.get("chunk_index"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def _evidence_refs(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for c in chunks:
        ref: dict[str, Any] = {
            "raw_document_id": c.get("raw_document_id"),
            "chunk_index": c.get("chunk_index"),
            "similarity": round(float(c.get("similarity", 0.0)), 4),
        }
        for key in ("title", "source_url", "securities_firm", "publish_date"):
            value = c.get(key)
            if value is not None:
                ref[key] = str(value)
        refs.append(ref)
    return refs


def _valuation_needs_review(quant: dict[str, Any]) -> bool:
    valuation = quant.get("valuation")
    if not isinstance(valuation, dict):
        return False
    return bool(valuation.get("needs_review")) or valuation.get("data_status") == "partial"


def _valuation_risk_flags(quant: dict[str, Any]) -> list[str]:
    valuation = quant.get("valuation")
    if not isinstance(valuation, dict):
        return []
    flags = valuation.get("risk_flags")
    if not isinstance(flags, list):
        return ["valuation_review_required"] if _valuation_needs_review(quant) else []
    return [str(flag) for flag in flags if str(flag or "").strip()]


def _merge_unique(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*first, *second]:
        text = str(value or "").strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _build_prompt(
    stock_code: str, chunks: list[dict[str, Any]], quant: dict[str, Any]
) -> str:
    payload = {
        "stock_code": stock_code,
        "coverage": COVERAGE,
        "report_quant": quant,
        "evidence_chunks": [
            _prompt_evidence_chunk(c)
            for c in chunks
        ],
    }
    instructions = (
        "당신은 증권사 리포트 근거(evidence_chunks)와 정량 메타(report_quant)를 종합하는 애널리스트입니다.\n"
        "증권사명으로 표본을 제한하지 않지만 네이버 금융 리서치 목록과 수집 기간 기준 표본이므로 시장 전체 컨센서스가 아닙니다.\n"
        "표본이 작으면 confidence를 낮추고 needs_review를 true로 두십시오. 근거에 없는 내용을 지어내지 마십시오.\n"
        "아래 JSON 입력만을 근거로, 반드시 다음 스키마의 JSON 객체 하나만 출력하십시오:\n"
        "{\n"
        '  "direction": "positive|neutral|negative|mixed",\n'
        '  "score": 0-100 정수,\n'
        '  "summary": "한국어 2~3문장 요약",\n'
        '  "key_rationale": ["핵심 근거 문장", ...],\n'
        '  "risk_flags": ["리스크 요인", ...],\n'
        '  "needs_review": true|false,\n'
        '  "confidence": 0-100 정수\n'
        "}\n\n"
        "입력:\n"
    )
    return instructions + json.dumps(payload, ensure_ascii=False, default=str)


def _prompt_evidence_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    item = {
        "raw_document_id": chunk.get("raw_document_id"),
        "chunk_index": chunk.get("chunk_index"),
        "similarity": round(float(chunk.get("similarity", 0.0)), 4),
        "text": str(chunk.get("chunk_text") or "")[:1200],
    }
    for key in ("title", "source_url", "securities_firm", "publish_date"):
        value = chunk.get(key)
        if value is not None:
            item[key] = str(value)
    return item


def parse_report_llm_response(response_text: str) -> dict[str, Any]:
    payload = _loads_json_object(response_text)

    direction = str(payload.get("direction") or "").strip().lower()
    if direction not in _ALLOWED_DIRECTIONS:
        direction = "unknown"

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ReportLlmError("report LLM 응답에 summary가 없습니다.")

    return {
        "direction": direction,
        # score는 0~100 정수 스케일. 1은 '100점 중 1점'인 유효 값이므로 0~1 환산을 적용하지 않는다.
        "score": _bounded_score(payload.get("score")),
        "summary": summary.strip(),
        "key_rationale": _string_list(payload.get("key_rationale")),
        "risk_flags": _string_list(payload.get("risk_flags")),
        "needs_review": bool(payload.get("needs_review")),
        # confidence는 모델이 0~1 확률로 줄 수 있어 그 경우만 100점 척도로 환산(DART와 동일).
        "confidence": _bounded_score(payload.get("confidence"), allow_fraction=True),
    }


def _loads_json_object(response_text: str) -> dict[str, Any]:
    text = (response_text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportLlmError("report LLM 응답이 유효한 JSON이 아닙니다.") from exc
    if not isinstance(payload, dict):
        raise ReportLlmError("report LLM 응답은 JSON 객체여야 합니다.")
    return payload


def _bounded_score(value: Any, *, allow_fraction: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50.0
    if allow_fraction and 0 < number <= 1:  # 0~1 확률로 온 경우만 100점 척도로
        number *= 100
    return float(max(0.0, min(100.0, number)))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
