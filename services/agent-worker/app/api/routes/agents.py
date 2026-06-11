"""
Agent 실행 엔드포인트
POST /agents/report  — Report RAG 단독 실행
POST /agents/analyze — 전체 에이전트 실행 (현재 report만, 추후 dart/alternative 추가)
"""
import dataclasses
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analyzers.report_analyzer import ReportAnalyzer
from app.collectors.report_collector import ReportCollector
from app.orchestrator.pipeline import AgentOrchestrator, SourcePipeline

router = APIRouter(prefix="/agents", tags=["agents"])

VALID_STOCK_CODES = {"005930", "000660", "035420"}


class AnalyzeRequest(BaseModel):
    stock_code: str = Field(..., examples=["005930"])
    stock_name: str | None = Field(None, examples=["삼성전자"])


def _pipeline_result_to_dict(result) -> dict:
    """dataclass SourceResult → JSON-직렬화 가능한 dict"""
    d = dataclasses.asdict(result)
    return d


@router.post("/report")
async def run_report_agent(req: AnalyzeRequest):
    if req.stock_code not in VALID_STOCK_CODES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 종목코드: {req.stock_code}")

    collector = ReportCollector()
    analyzer = ReportAnalyzer()

    evidence = collector.collect(req.stock_code)
    result = analyzer.analyze(req.stock_code, evidence)

    return {
        "stock_code": req.stock_code,
        "stock_name": req.stock_name,
        "source": "report",
        "result": _pipeline_result_to_dict(result),
    }


@router.post("/analyze")
async def run_analyze(req: AnalyzeRequest):
    """전체 에이전트 실행. 현재 report만 구현, dart/alternative는 추후 추가."""
    if req.stock_code not in VALID_STOCK_CODES:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 종목코드: {req.stock_code}")

    report_pipeline = SourcePipeline(
        source="report",
        collector=ReportCollector(),
        analyzer=ReportAnalyzer(),
    )
    orchestrator = AgentOrchestrator(pipelines=[report_pipeline])
    results = orchestrator.run(req.stock_code)

    return {
        "stock_code": req.stock_code,
        "stock_name": req.stock_name,
        "agent_results": {
            src: _pipeline_result_to_dict(res) for src, res in results.items()
        },
    }
