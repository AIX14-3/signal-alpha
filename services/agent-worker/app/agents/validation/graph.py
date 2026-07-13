"""LangGraph wrapper for the data-quality validation agent.

datalab cause 그래프와 동형 패턴: 로직은 ``DataQualityAgent`` (langgraph 없이 동작),
그래프는 결정을 1급 조건부 엣지로 노출한다 — **결정론 프로파일에 이상이 없고 LLM 도
없으면 LLM 검토를 건너뛴다**(조용한 날 비용 0).

    profile ─[anomaly or llm?]─▶ llm_review ─▶ finalize ─▶ END
        └───────(clean & no llm)────────────────┘

검증은 점수를 바꾸지 않는다 — needs_review/risk_flags 승격 재료와 validation_logs
기록만 낸다(SCORE_COHORT 핸들러가 소비).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.validation.agent import DataQualityAgent, StockValidation

VALIDATION_GRAPH_NAME = "data_quality_v1"


class ValidationGraphState(TypedDict, total=False):
    source: str
    asof: date
    pit_by_ticker: dict[str, list[dict]]
    scored: dict[str, dict[str, Any]] | None
    profiles: dict[str, dict[str, Any]]
    verdicts: list[StockValidation]
    graph_nodes: list[str]


class ValidationGraphAgent:
    def __init__(self, *, agent: DataQualityAgent | None = None, client: Any | None = None) -> None:
        self._agent = agent or DataQualityAgent(client)
        self._graph = self._build_graph()

    @property
    def llm_model(self) -> str | None:
        return self._agent.llm_model

    async def validate(
        self,
        *,
        source: str,
        asof: date,
        pit_by_ticker: dict[str, list[dict]],
        scored: dict[str, dict[str, Any]] | None = None,
    ) -> list[StockValidation]:
        state = await self._graph.ainvoke(
            {
                "source": source,
                "asof": asof,
                "pit_by_ticker": pit_by_ticker,
                "scored": scored,
                "graph_nodes": [],
            }
        )
        return state["verdicts"]

    def _build_graph(self):
        graph = StateGraph(ValidationGraphState)
        graph.add_node("profile", self._profile)
        graph.add_node("llm_review", self._llm_review)
        graph.add_node("finalize", self._finalize)
        graph.set_entry_point("profile")
        graph.add_conditional_edges(
            "profile", self._needs_llm, {"llm_review": "llm_review", "finalize": "finalize"}
        )
        graph.add_edge("llm_review", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    async def _profile(self, state: ValidationGraphState) -> dict[str, Any]:
        profiles = self._agent.profile(state["source"], state["asof"], state["pit_by_ticker"])
        return {"profiles": profiles, "graph_nodes": [*state.get("graph_nodes", []), "profile"]}

    def _needs_llm(self, state: ValidationGraphState) -> Literal["llm_review", "finalize"]:
        # LLM 이 배선돼 있으면 항상 검토(분석 적절성은 프로파일로 못 본다). 없으면
        # 결정론 프로파일만으로 finalize.
        return "llm_review" if self._agent.llm_model else "finalize"

    async def _llm_review(self, state: ValidationGraphState) -> dict[str, Any]:
        verdicts = await self._agent.review(
            source=state["source"],
            asof=state["asof"],
            profiles=state["profiles"],
            scored=state.get("scored"),
        )
        return {"verdicts": verdicts, "graph_nodes": [*state.get("graph_nodes", []), "llm_review"]}

    async def _finalize(self, state: ValidationGraphState) -> dict[str, Any]:
        verdicts = state.get("verdicts")
        if verdicts is None:  # 결정론 전용 경로
            verdicts = await self._agent.review(
                source=state["source"], asof=state["asof"], profiles=state["profiles"]
            )
        return {"verdicts": verdicts, "graph_nodes": [*state.get("graph_nodes", []), "finalize"]}
