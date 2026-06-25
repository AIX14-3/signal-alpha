"""큐 태스크 페이로드 파싱 공용 헬퍼.

게이트형 파이프라인 핸들러(risk_veto·meta_combine·synthesis 등)가 공유하는 작은 파서.
``task_context`` 는 dict 또는 JSON 문자열로, id 목록은 list/JSON/PG 배열 리터럴(``{1,2,3}``)로
들어올 수 있어 핸들러마다 복붙되던 로직을 한 곳으로 모은다.
"""

from __future__ import annotations

import json
from typing import Any

from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL


async def enqueue_aggregate(
    queue: Any,
    *,
    stock_id: int,
    aggregate_ctx: dict[str, Any] | None,
    priority: str = "batch",
) -> int | None:
    """선형 체인에서 게이트2(AGGREGATE_SIGNAL)를 인큐한다.

    ``aggregate_ctx`` 는 DartAnalyze가 만들어 ML_INFER→META_COMBINE 를 불투명하게 통과시킨
    컨텍스트(``signal_date``·``stock_code``·``aggregation_key``). AGGREGATE는 이제 fan-in
    방식이라 ``source_analysis_result_ids`` 없이도 (stock_id, signal_date)로 모든 소스를 모은다 —
    ids 가 있으면 그 목록만 집계하는 레거시 단일 프로듀서 경로로 동작한다.
    """
    if not aggregate_ctx:
        return None
    ids = aggregate_ctx.get("source_analysis_result_ids")
    ctx = {key: value for key, value in aggregate_ctx.items() if key != "source_analysis_result_ids"}
    ctx.setdefault("priority", priority)
    return await queue.enqueue(
        stock_id=stock_id,
        task_type=AGGREGATE_SIGNAL,
        priority=priority,
        source_analysis_result_ids=ids,
        task_context=ctx,
        dedupe=True,
    )


def parse_task_context(value: Any) -> dict[str, Any]:
    """task_context를 dict로 정규화(None→{}, JSON 문자열→dict)."""
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def parse_int_list(value: Any) -> list[int]:
    """id 목록을 list[int]로 정규화 — list/tuple, JSON 배열, PG 배열 리터럴(``{1,2}``) 지원."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            return [int(item.strip()) for item in inner.split(",") if item.strip()]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]
