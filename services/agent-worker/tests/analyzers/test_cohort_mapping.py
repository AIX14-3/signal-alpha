"""StockScore → SourceResult 매핑 계약 — 스케일 무변환·no_signal·provenance."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "packages" / "data-access"))

from app.analyzers.cohort.mapping import to_source_result
from app.analyzers.llm_scorer import PROMPT_VERSION, StockScore
from app.orchestrator.alternative_persistence import _method_detail, _to_100


def _score(**overrides):
    base = dict(
        ticker="005930",
        score=0.42,
        confidence=0.7,
        no_signal=False,
        evidence=["순매도 4,530만주", "월별 이벤트 증가"],
        score_change_reason=None,
    )
    base.update(overrides)
    return StockScore(**base)


def test_score_passes_through_unscaled():
    result = to_source_result(_score(), source="DART", stock_code="005930", llm_model="gemini-2.5-flash")
    assert result.score == 0.42  # [-1,1] 그대로 — 변환은 write 경계(_to_100)에서만
    assert result.direction == "positive"
    assert result.data_status == "ok"


def test_write_boundary_is_the_only_scale_conversion():
    result = to_source_result(_score(score=-0.5), source="DART", stock_code="005930", llm_model="m")
    assert _to_100(result.score) == 25.0  # (-0.5+1)*50


def test_no_signal_maps_to_data_status_and_zero_score():
    result = to_source_result(
        _score(score=0.0, no_signal=True), source="REPORT", stock_code="005930", llm_model="m"
    )
    assert result.data_status == "no_signal"
    assert result.score == 0.0
    assert result.direction == "neutral"
    assert "기권" in result.summary


def test_llm_provenance_flows_to_method_detail():
    result = to_source_result(
        _score(score_change_reason="목표주가 하향 반전"),
        source="HIRING",
        stock_code="005930",
        llm_model="gemini-2.5-flash",
    )
    assert result.analysis_source == "llm"
    assert result.prompt_ver == PROMPT_VERSION
    assert result.llm_model == "gemini-2.5-flash"
    detail = _method_detail(result)
    assert detail["analysis_source"] == "llm"
    assert detail["llm_confidence"] == 0.7
    assert detail["score_change_reason"] == "목표주가 하향 반전"


def test_prompt_ver_fits_varchar20():
    # agent_results.prompt_ver varchar(20) — 특허 22자 초과 버그(PR #820)의 재발 방지 가드.
    assert len(PROMPT_VERSION) <= 20


def test_evidence_items_capped_at_five():
    result = to_source_result(
        _score(evidence=[f"근거 {i}" for i in range(9)]),
        source="PATENT",
        stock_code="005930",
        llm_model="m",
    )
    assert len(result.evidence_items) == 5
    assert result.evidence_items[0].title == "근거 0"
