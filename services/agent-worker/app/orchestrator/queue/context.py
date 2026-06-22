"""큐 태스크 페이로드 파싱 공용 헬퍼.

게이트형 파이프라인 핸들러(risk_veto·meta_combine·synthesis 등)가 공유하는 작은 파서.
``task_context`` 는 dict 또는 JSON 문자열로, id 목록은 list/JSON/PG 배열 리터럴(``{1,2,3}``)로
들어올 수 있어 핸들러마다 복붙되던 로직을 한 곳으로 모은다.
"""

from __future__ import annotations

import json
from typing import Any


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
