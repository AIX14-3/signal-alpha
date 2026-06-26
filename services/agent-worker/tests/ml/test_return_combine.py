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


def _inf(model_name, pred, *, gate=True):
    return {"model_name": model_name, "pred_value": pred, "gate_passed": gate}


def test_return_combine_persists_return_columns():
    inferences = _FakeInferences(
        [_inf("src_datalab", 0.03), _inf("src_hiring", 0.05)]
    )
    meta = _FakeMeta()
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=inferences,
        meta=meta,
        collection=_FakeCollection(),
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

    assert len(meta.calls) == 1
    call = meta.calls[0]
    assert call["run_key"] == "SRC"
    assert call["combined_vol"] is None  # vol 채널 불변(D4)
    assert call["final_score"] == 0.04
    assert call["direction"] == "positive"


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
        connection=object(), inferences=inferences, meta=meta, collection=_FakeCollection()
    )

    result = asyncio.run(handler({"stock_id": 1, "task_context": {"as_of": "2026-06-01"}}))

    # src_datalab 한 개만 결합 → 단일 base, confidence 0.5.
    assert result["final_score"] == 0.03
    assert result["model_count"] == 1
    assert result["confidence"] == 0.5


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
        return_model=model,
    )

    result = asyncio.run(handler({"stock_id": 2, "task_context": {"as_of": "2026-06-01"}}))

    assert result["method"] == "linear_stacking"
    # peer_gap_avg = implied(12) - applied(10) = 2 → final = 0.1 - 2.0 = -1.9
    assert result["final_score"] == -1.9
    assert result["direction"] == "negative"


def test_return_combine_requires_asof():
    handler = ReturnCombineTaskHandler(
        connection=object(),
        inferences=_FakeInferences([]),
        meta=_FakeMeta(),
        collection=_FakeCollection(),
    )
    result = asyncio.run(handler({"stock_id": 1, "task_context": {}}))
    assert result["skipped_reason"] == "asof_date_required"
