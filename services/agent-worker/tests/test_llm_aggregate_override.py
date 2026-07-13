"""LLM 통합 판정 오버라이드 — LLM 소유 필드 vs 결정론 유지 필드의 경계 계약."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "data-access"))

from app.orchestrator.aggregation.llm_aggregate import (
    build_per_source_scores,
    maybe_llm_aggregate,
)


def _settings(**overrides):
    base = dict(
        llm_aggregate_enabled=True,
        llm_scoring_provider="vertex",
        llm_scoring_model="gemini-2.5-flash",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _coarse_row(**overrides):
    base = dict(
        source="DART",
        score=-0.7,
        data_status="ok",
        summary="순매도 발견",
        highlights=["순매도 4,530만주"],
        llm_confidence=0.6,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _aggregate(**overrides):
    base = dict(
        signal="neutral",
        final_score=50.0,
        scoring_count=2,
        blend_basis={"sources": []},
        summary="결정론 요약",
        positive_evidence=["결정론 긍정"],
        caution_evidence=["DATALAB: 데이터 정체(오래됨)"],
        needs_review=False,
        consensus_score=75.0,
        source_agreement="MEDIUM",
        warning_level="NORMAL",
    )
    base.update(overrides)
    return base


class _FakeClient:
    model = "fake-model"

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    async def generate_json(self, prompt, schema=None):
        if self._error is not None:
            raise self._error
        return self._payload


def _verdict_payload():
    return {
        "verdicts": [
            {
                "ticker": "005930",
                "final_score": -0.4,
                "confidence": 0.5,
                "signal": "negative",
                "conflict": True,
                "headline": "지분 감소가 두드러집니다",
                "positive_evidence": [],
                "caution_evidence": ["대량 순매도"],
                "contributing": [{"source": "DART", "weight": 1.0, "why": "유일 신호"}],
            }
        ]
    }


def test_build_per_source_excludes_failed_and_flags_no_signal():
    coarse = [
        _coarse_row(),
        _coarse_row(source="PRICE", data_status="failed"),
        _coarse_row(source="REPORT", data_status="no_signal", highlights=[]),
    ]
    scores = build_per_source_scores(coarse, "005930")
    assert set(scores) == {"DART", "REPORT"}
    assert scores["REPORT"].no_signal is True
    assert scores["REPORT"].evidence == ["순매도 발견"]  # highlights 없으면 summary 폴백


def test_flag_off_returns_aggregate_unchanged():
    aggregate = _aggregate()
    out = asyncio.run(
        maybe_llm_aggregate(
            _settings(llm_aggregate_enabled=False),
            ticker="005930", name="삼성전자", signal_date="2026-07-13",
            coarse=[_coarse_row()], aggregate=aggregate,
        )
    )
    assert out is aggregate


def test_success_overrides_llm_fields_and_keeps_deterministic_ones():
    out = asyncio.run(
        maybe_llm_aggregate(
            _settings(),
            ticker="005930", name="삼성전자", signal_date="2026-07-13",
            coarse=[_coarse_row()], aggregate=_aggregate(),
            client_factory=lambda: _FakeClient(payload=_verdict_payload()),
        )
    )
    # LLM 소유 — 오버라이드. final_score 는 반드시 0-100(score_100) 스케일.
    assert out["signal"] == "negative"
    assert out["final_score"] == 30.0  # (-0.4+1)*50
    assert out["summary"] == "지분 감소가 두드러집니다"
    assert out["blend_basis"]["scoring_method"] == "llm_judgment"
    assert out["needs_review"] is True  # conflict → 검토 플래그
    # 결정론 유지 — consensus/agreement/warning 은 그대로. LLM confidence 는 _meta 재료로만.
    assert out["consensus_score"] == 75.0
    assert out["source_agreement"] == "MEDIUM"
    assert out["warning_level"] == "NORMAL"
    assert out["llm_aggregate"]["confidence"] == 0.5
    # FE 계약 보존: 근거 리스트(구조화 dict)는 결정론 그대로 — LLM 근거 문장은 _meta 로만.
    assert out["positive_evidence"] == ["결정론 긍정"]
    assert out["caution_evidence"] == ["DATALAB: 데이터 정체(오래됨)"]
    assert out["llm_aggregate"]["caution_evidence"] == ["대량 순매도"]


def test_llm_failure_keeps_deterministic_blend_and_records_error():
    aggregate = _aggregate()
    out = asyncio.run(
        maybe_llm_aggregate(
            _settings(),
            ticker="005930", name="삼성전자", signal_date="2026-07-13",
            coarse=[_coarse_row()], aggregate=aggregate,
            client_factory=lambda: _FakeClient(error=RuntimeError("429 quota")),
        )
    )
    assert out["signal"] == "neutral"
    assert out["final_score"] == 50.0
    assert "429" in out["llm_aggregate_error"]
    assert "llm_aggregate" not in out


def test_no_scoring_sources_skips_llm_entirely():
    aggregate = _aggregate(scoring_count=0)
    out = asyncio.run(
        maybe_llm_aggregate(
            _settings(),
            ticker="005930", name="삼성전자", signal_date="2026-07-13",
            coarse=[_coarse_row()], aggregate=aggregate,
            client_factory=lambda: _FakeClient(error=AssertionError("must not be called")),
        )
    )
    assert out is aggregate
