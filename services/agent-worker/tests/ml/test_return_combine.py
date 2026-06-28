"""RETURN_COMBINE 핸들러 (#525 WS-C) — DB 없이 fake 로 검증.

src_* 예측(run_key=SRC) + Report 피처 → combine_return → meta_signals(run_key=SRC) return 컬럼.
"""

from __future__ import annotations

import asyncio
from datetime import date

from app.ml.return_combine import ReturnCombineTaskHandler


class _FakeInferences:
    def __init__(self, rows):
        self._rows = rows
        self.queried = None

    async def list_for_run(self, *, stock_id, run_key, asof_date, horizon):
        self.queried = {"stock_id": stock_id, "run_key": run_key, "asof_date": asof_date, "horizon": horizon}
        return self._rows


class _FakeCollection:
    def __init__(self, facts=()):
        self._facts = facts

    async def list_report_valuation_facts(self, *, stock_id):
        return list(self._facts)


class _FakeMeta:
    def __init__(self):
        self.calls = []

    async def upsert_meta_signal(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class _FakeAnalysis:
    def __init__(self, *, found=True):
        self.calls = []
        self._found = found

    async def update_final_signal_return_channel(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": 1, **kwargs} if self._found else None


def _inf(model_name, pred, *, gate=True):
    return {"model_name": model_name, "pred_value": pred, "gate_passed": gate}


def test_return_combine_persists_return_columns():
    inferences = _FakeInferences(
        [_inf("src_datalab", 0.03), _inf("src_hiring", 0.05)]
    )
    meta = _FakeMeta()
    analysis = _FakeAnalysis()
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(),
        analysis=analysis,
        return_model=None,  # 폴백: base 예측 균등 평균
    )

    result = asyncio.run(
        handler({"stock_id": 7, "task_context": {"as_of": "2026-06-01", "horizon": 20}})
    )

    # run_key=SRC, horizon=20 으로 src_* 를 조회.
    assert inferences.queried == {"stock_id": 7, "run_key": "SRC", "asof_date": date(2026, 6, 1), "horizon": 20}
    assert result["final_score"] == 0.04  # (0.03+0.05)/2
    assert result["direction"] == "positive"
    assert result["confidence"] == 1.0

    # 소스별 예측률 + 통합(SRC) 적재. 주가(src_price) 부재 → datalab/hiring 단독.
    by_run = {c["run_key"]: c for c in meta.calls}
    integrated = by_run["SRC"]
    assert integrated["combined_vol"] is None  # vol 채널 불변(D4)
    assert integrated["final_score"] == 0.04
    assert integrated["direction"] == "positive"
    assert {"SRC_DATALAB", "SRC_HIRING"} <= set(by_run)
    assert by_run["SRC_DATALAB"]["final_score"] == 0.03
    assert by_run["SRC_HIRING"]["final_score"] == 0.05

    # final_signals 에 return 채널 오버레이.
    assert len(analysis.calls) == 1
    overlay = analysis.calls[0]
    assert overlay == {
        "stock_id": 7,
        "ml_final_score": 0.04,
        "ml_direction": "positive",
        "ml_confidence": 1.0,
    }
    assert result["final_signal_overlaid"] is True


def test_return_combine_ignores_non_src_and_gated_rows():
    inferences = _FakeInferences(
        [
            _inf("src_datalab", 0.03),
            _inf("ewma", 0.2),  # vol 모델 — return 채널 제외
            _inf("src_hiring", None),  # 결측 제외
            _inf("src_hiring", 0.9, gate=False),  # gate 미통과 제외
        ]
    )
    meta = _FakeMeta()
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(),
        analysis=_FakeAnalysis(),
    )

    result = asyncio.run(handler({"stock_id": 1, "task_context": {"as_of": "2026-06-01"}}))

    # src_datalab 한 개만 결합 → 단일 base, confidence 0.5.
    assert result["final_score"] == 0.03
    assert result["model_count"] == 1
    assert result["confidence"] == 0.5


def test_return_combine_includes_src_dart():
    # src_dart 예측이 SOURCE_MODELS 에 있으므로 return 채널 결합에 자동 합류(#546 Phase 4).
    inferences = _FakeInferences(
        [
            _inf("src_datalab", 0.03),
            _inf("src_dart", 0.05),
            _inf("ewma", 0.9),  # vol 모델 — 여전히 제외
        ]
    )
    meta = _FakeMeta()
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(),
        analysis=_FakeAnalysis(),
        return_model=None,  # 폴백: base 예측 균등 평균
    )

    result = asyncio.run(handler({"stock_id": 3, "task_context": {"as_of": "2026-06-01"}}))

    # src_datalab + src_dart 평균 → vol 모델 ewma 는 제외.
    assert result["model_count"] == 2
    assert result["final_score"] == 0.04  # (0.03+0.05)/2


def test_return_combine_uses_report_features_with_linear_model():
    inferences = _FakeInferences([_inf("src_datalab", 0.1)])
    meta = _FakeMeta()
    facts = [
        {"publish_date": "2026-05-15", "implied_multiple": 12.0, "applied_multiple": 10.0},
    ]
    # 선형 모델: src_datalab 양의 기여 + report__peer_gap_avg 음의 기여.
    model = {"intercept": 0.0, "coef": {"src_datalab": 1.0, "report__peer_gap_avg": -1.0}}
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(facts),
        analysis=_FakeAnalysis(),
        return_model=model,
    )

    result = asyncio.run(handler({"stock_id": 2, "task_context": {"as_of": "2026-06-01"}}))

    assert result["method"] == "linear_stacking"
    # peer_gap_avg = implied(12) - applied(10) = 2 → final = 0.1 - 2.0 = -1.9
    assert result["final_score"] == -1.9
    assert result["direction"] == "negative"


def test_per_source_fusion_anchors_price():
    # C안 핵심: 주가 BASE 가 각 소스에 앵커로 포함된다(주가 ⊕ 소스). 주가 단독도 6개 중 하나.
    inferences = _FakeInferences(
        [_inf("src_price", 0.02), _inf("src_datalab", 0.04), _inf("src_patent", -0.01)]
    )
    meta = _FakeMeta()
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(),
        analysis=_FakeAnalysis(),
        return_model=None,  # 폴백: 균등 평균
    )

    result = asyncio.run(handler({"stock_id": 4, "task_context": {"as_of": "2026-06-01"}}))

    by_run = {c["run_key"]: c for c in meta.calls}
    assert {"SRC_PRICE", "SRC_DATALAB", "SRC_PATENT", "SRC"} <= set(by_run)
    # 주가 단독 = src_price 만(앵커 자기 자신).
    assert by_run["SRC_PRICE"]["final_score"] == 0.02
    assert by_run["SRC_PRICE"]["model_count"] == 1
    # datalab 예측률 = 주가 ⊕ datalab → (0.02+0.04)/2 = 0.03, base 2개.
    assert by_run["SRC_DATALAB"]["final_score"] == 0.03
    assert by_run["SRC_DATALAB"]["model_count"] == 2
    # patent 예측률 = 주가 ⊕ patent → (0.02-0.01)/2 = 0.005.
    assert by_run["SRC_PATENT"]["final_score"] == 0.005
    assert by_run["SRC_PATENT"]["model_count"] == 2
    # 통합 = 주가+datalab+patent (base 3개).
    assert by_run["SRC"]["model_count"] == 3
    # result 최상위 = 통합, per_source 요약 노출.
    assert result["per_source"]["SRC_PRICE"] == 0.02
    assert result["per_source"]["SRC_DATALAB"] == 0.03


def test_return_combine_requires_asof():
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=_FakeInferences([]),
        meta=_FakeMeta(),
        collection=_FakeCollection(),
    )
    result = asyncio.run(handler({"stock_id": 1, "task_context": {}}))
    assert result["skipped_reason"] == "asof_date_required"
