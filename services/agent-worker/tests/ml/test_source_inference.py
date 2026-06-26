"""SRC_INFER 핸들러 (#525 Phase 3 배선) — DB 없이 fake 로더/리포로 검증.

확인 사항: PIT 피처 어셈블 → base 모델 예측 → ml_inferences(run_key=SRC) 적재,
미적재 모델은 결측(gate_passed=False, error=model_unavailable)으로 기록, vol META 미인큐.
"""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from app.ml.source_inference import SrcInferTaskHandler
from app.ml.source_models import SOURCE_RUN_KEY, SourceModel


class _FakeBooster:
    """피처와 무관하게 고정값을 돌려주는 LightGBM Booster 대역."""

    def __init__(self, value: float) -> None:
        self._value = value

    def predict(self, rows):  # noqa: ANN001 - lgb.Booster.predict 시그니처 모사
        return [self._value for _ in rows]


class _FakeLoader:
    def __init__(self, rows, *, sector_demand=None):
        self._rows = rows
        self._sector_demand = sector_demand

    async def load(self, *, stock_id, stock_code, as_of):  # noqa: ANN001
        metadata = {"rows": self._rows, "sector_demand": self._sector_demand}
        return [SimpleNamespace(metadata=metadata)]


class _FakeInferences:
    def __init__(self):
        self.calls = []

    async def upsert_inference(self, **kwargs):
        self.calls.append(kwargs)


class _FakeQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, **kwargs):
        self.enqueued.append(kwargs)
        return len(self.enqueued)


def _dl_row(observed: str, index: float) -> dict:
    return {
        "observed_date": observed,
        "search_index": index,
        "change_pct": 0.05,
        "is_spike": False,
        "polarity": "demand",
        "weight": 1.0,
    }


def test_src_infer_persists_predictions_under_src_run_key():
    as_of = date(2026, 6, 1)
    datalab_loader = _FakeLoader(
        [_dl_row("2026-05-20", 100.0), _dl_row("2026-06-10", 999.0)]  # 미래행은 PIT 로 제외됨
    )
    hiring_loader = _FakeLoader(
        [{"observed_date": "2026-05-25", "job_count": 12, "change_pct": 0.1}],
        sector_demand={"momentum_pct": 0.2, "coverage": 0.5},
    )
    inferences = _FakeInferences()
    queue = _FakeQueue()
    # datalab 모델만 적재(고정 예측), hiring 모델은 미적재(아티팩트 부재 시뮬레이션).
    models = [
        SourceModel("src_datalab").attach(_FakeBooster(0.0123)),
        SourceModel("src_hiring"),  # unloaded → predict None
    ]

    handler = SrcInferTaskHandler(
        connection=object(),
        datalab_loader=datalab_loader,
        hiring_loader=hiring_loader,
        inferences=inferences,
        queue=queue,
        models=models,
    )

    result = asyncio.run(
        handler(
            {
                "stock_id": 7,
                "task_context": {"stock_code": "005930", "as_of": "2026-06-01"},
            }
        )
    )

    assert result["run_key"] == SOURCE_RUN_KEY == "SRC"
    assert result["succeeded"] == ["src_datalab"]
    assert result["persisted"] == 2

    # 성공 예측이 있으니 return 채널 결합(RETURN_COMBINE)을 인큐.
    assert len(queue.enqueued) == 1
    enq = queue.enqueued[0]
    assert enq["task_type"] == "return_combine"
    assert enq["task_context"]["run_key"] == "SRC"
    assert enq["task_context"]["as_of"] == "2026-06-01"
    assert result["return_combine_task_id"] == 1

    by_model = {c["model_name"]: c for c in inferences.calls}
    assert set(by_model) == {"src_datalab", "src_hiring"}

    datalab = by_model["src_datalab"]
    assert datalab["run_key"] == "SRC"
    assert datalab["asof_date"] == as_of
    assert datalab["pred_value"] == 0.0123
    assert datalab["gate_passed"] is True
    assert datalab["error_message"] is None

    hiring = by_model["src_hiring"]
    assert hiring["pred_value"] is None
    assert hiring["gate_passed"] is False
    assert hiring["error_message"] == "model_unavailable"


def test_src_infer_handles_no_models_loaded():
    inferences = _FakeInferences()
    queue = _FakeQueue()
    handler = SrcInferTaskHandler(
        connection=object(),
        datalab_loader=_FakeLoader([]),
        hiring_loader=_FakeLoader([]),
        inferences=inferences,
        queue=queue,
        models=[SourceModel("src_datalab"), SourceModel("src_hiring")],
    )

    result = asyncio.run(handler({"stock_id": 1, "task_context": {"as_of": "2026-06-01"}}))

    assert result["succeeded"] == []
    assert all(c["pred_value"] is None for c in inferences.calls)
    assert all(c["run_key"] == "SRC" for c in inferences.calls)
    # 성공 예측이 없으면 return 채널 결합을 인큐하지 않는다.
    assert queue.enqueued == []
    assert result["return_combine_task_id"] is None
