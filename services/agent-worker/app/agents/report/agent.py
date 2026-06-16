"""Report RAG 분석 Agent (SourceAnalysisAgent 구현).

main의 Source Agent 공통 계약(app/agents/base.py)을 따른다. DART agent와 동일하게
agent는 **순수 분석**만 한다 — DB·임베딩은 직접 다루지 않고, 주입된 retriever와
input_data.context(정량 메타)로만 동작한다.

흐름:
  1. 다중 질의(QUESTIONS)로 stock_id 격리 RAG 검색 → 청크 합집합·중복제거
  2. input_data.context["report_quant"] 의 정량 메타(목표주가/의견 분포 등) 결합
  3. LLM 종합(provider는 dart/llm.py의 LlmClient 재사용) → SourceAgentOutput
     - LLM 미설정/실패/근거 없음 → 보수적 fallback(needs_review, data_status 강등)

결정 B(2026-06-15): 신한·미래에셋·유진 3사만 수집 → 표본 작음. 컨센서스/conflict 신호는
약하므로 점수보다 **근거 청크**를 신뢰하고 needs_review를 보수적으로. 한계를
method_detail.coverage에 기계가 읽을 수 있게 박는다(사용자 면책은 Phase 5 final_signals).
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

# 결정 B — 3사 한정 커버리지(기계가 읽는 메타). 사용자 면책은 final_signals disclaimer에서.
COVERAGE = {
    "firms": ["신한투자증권", "미래에셋증권", "유진투자증권"],
    "note": "3개 증권사 한정 — 시장 전체 컨센서스 아님",
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

        data_status: SourceAgentStatus = "partial" if analysis["needs_review"] else "ok"
        return SourceAgentOutput(
            source="REPORT",
            stock_code=stock_code,
            direction=analysis["direction"],
            score=float(analysis["score"]),
            summary=analysis["summary"],
            risk_flags=analysis["risk_flags"],
            method_detail={
                "coverage": COVERAGE,
                "key_rationale": analysis["key_rationale"],
                "report_quant": quant,
                "evidence_chunks": _evidence_refs(chunks),
                "llm_confidence": analysis["confidence"],
            },
            needs_review=analysis["needs_review"],
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
    ) -> SourceAgentOutput:
        # 표본 작음 + 종합 불가 → 점수 중립, needs_review로 사람 검토 유도(결정 B).
        return SourceAgentOutput(
            source="REPORT",
            stock_code=stock_code,
            direction="unknown",
            score=50.0,
            summary=summary,
            risk_flags=[],
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
    return [
        {
            "raw_document_id": c.get("raw_document_id"),
            "chunk_index": c.get("chunk_index"),
            "similarity": round(float(c.get("similarity", 0.0)), 4),
        }
        for c in chunks
    ]


def _build_prompt(
    stock_code: str, chunks: list[dict[str, Any]], quant: dict[str, Any]
) -> str:
    payload = {
        "stock_code": stock_code,
        "coverage": COVERAGE,
        "report_quant": quant,
        "evidence_chunks": [
            {
                "raw_document_id": c.get("raw_document_id"),
                "chunk_index": c.get("chunk_index"),
                "similarity": round(float(c.get("similarity", 0.0)), 4),
                "text": str(c.get("chunk_text") or "")[:1200],
            }
            for c in chunks
        ],
    }
    instructions = (
        "당신은 증권사 리포트 근거(evidence_chunks)와 정량 메타(report_quant)를 종합하는 애널리스트입니다.\n"
        "신한·미래에셋·유진 3개 증권사 리포트만 수집된 표본이므로 시장 전체 컨센서스가 아닙니다.\n"
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
        "score": _bounded_score(payload.get("score")),
        "summary": summary.strip(),
        "key_rationale": _string_list(payload.get("key_rationale")),
        "risk_flags": _string_list(payload.get("risk_flags")),
        "needs_review": bool(payload.get("needs_review")),
        "confidence": _bounded_score(payload.get("confidence")),
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


def _bounded_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 50.0
    if 0 < number <= 1:  # 0~1 확률로 오면 100점 척도로
        number *= 100
    return float(max(0.0, min(100.0, number)))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
