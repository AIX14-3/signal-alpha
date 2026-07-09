"""Adapt a rule-only ``Analyzer`` to the ``SourceAnalysisAgent`` contract.

The Alternative harness analyzers (HIRING, PATENT, DATALAB) implement the
collector-era ``Analyzer`` protocol — ``analyze(stock_code, evidence) ->
SourceResult``. ``RuleSourceAgent`` wraps any of them so they can be driven
through the higher-level ``SourceAgentInput`` / ``SourceAgentOutput`` contract
used by DART (and, later, the LangGraph workflow) without touching the analyzer.

Scoring is unchanged: the ``SourceResult`` numbers flow straight through.
``analysis_source`` is always ``"rules"``; ``llm_model`` carries forward any
offline LLM provenance the analyzer attached (e.g. DataLab polarity) so the
Scope A provenance invariant holds — the LLM never alters the score here.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.analyzers.base import Analyzer
from app.schemas.evidence import SourceType
from app.schemas.source_result import SourceResult


class RuleSourceAgent:
    source: SourceType

    def __init__(self, analyzer: Analyzer, *, prompt_ver: str | None = None) -> None:
        self._analyzer = analyzer
        self.source = analyzer.source
        self.prompt_ver = prompt_ver or f"{str(analyzer.source).lower()}-rules-v1"

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        result = await self._analyzer.analyze(
            input_data.stock_code,
            input_data.evidence,
        )
        return _to_output(result, self.prompt_ver)


def _to_output(result: SourceResult, prompt_ver: str) -> SourceAgentOutput:
    method_detail: dict[str, Any] = {"data_status": result.data_status}
    if result.evidence_items:
        method_detail["evidence_items"] = [asdict(item) for item in result.evidence_items]
    if result.report_meta is not None:
        method_detail["report_meta"] = asdict(result.report_meta)
    # Neutral attention-spike layer (DATALAB): stash the five structured fields as
    # one sub-dict so the orchestrator round-trip (``_from_output``) restores them
    # — otherwise ``attention_note`` never reaches the aggregator's "주의 근거"
    # routing nor persistence. Written only when any field is set, so every
    # non-DataLab source keeps its method_detail shape unchanged.
    attention = {
        "attention_tier": result.attention_tier,
        "attention_z": result.attention_z,
        "attention_note": result.attention_note,
        "expected_fwd_vol_mult": result.expected_fwd_vol_mult,
        "expected_fwd_volume_mult": result.expected_fwd_volume_mult,
    }
    if any(value is not None for value in attention.values()):
        method_detail["attention"] = attention
    # 표시 전용 구조화 데이터(PATENT 공개 특허·출원 추이 / HIRING 공고 게시일·마감일).
    # 이 왕복(_to_output → _to_source_result)이 싣지 않으면 persistence 가 보는
    # SourceResult 에서 사라져 score_breakdown 까지 절대 도달하지 못한다.
    # 점수·방향과 무관하며, 값이 없는 소스는 키 자체를 남기지 않아 기존 모양이 그대로다.
    if result.patent_meta is not None:
        method_detail["patent"] = asdict(result.patent_meta)
    if result.hiring_meta is not None:
        method_detail["hiring"] = asdict(result.hiring_meta)
    return SourceAgentOutput(
        source=result.source,
        stock_code=result.stock_code,
        direction=result.direction,
        score=result.score,
        summary=result.summary,
        risk_flags=list(result.risk_flags),
        method_detail=method_detail,
        needs_review=result.data_status in ("partial", "failed"),
        data_status=result.data_status,
        analysis_source="rules",
        llm_model=result.llm_model,
        prompt_ver=prompt_ver,
    )
