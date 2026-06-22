"""리스크 veto — architecture.mermaid의 "리스크 veto (치명 키워드 차단)".

게이트2(=AGGREGATE_SIGNAL) 통과로 발행된 신호라도, 종목의 증거 텍스트에 치명 키워드
(상장폐지·감사의견거절·횡령 등)가 있으면 발행을 보류한다. 결정론적 단계로, 점수/방향은
그대로 두고 ``final_signals`` 의 발행 플래그만 차단(is_published=FALSE, needs_review=TRUE,
warning_level=WARNING)하고 사유를 ``validation_logs`` 에 남긴다.

``scan_for_veto`` 는 DB 미접근 순수 함수(단위 테스트 용이). ``RiskVetoTaskHandler`` 는 큐의
``RISK_VETO`` 핸들러로, AGGREGATE_SIGNAL이 발행 신호에 대해 enqueue 한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.gates.rules.veto_keywords import veto_keywords


@dataclass(frozen=True)
class VetoDecision:
    vetoed: bool
    matched_keywords: list[str]


def scan_for_veto(
    texts: Iterable[str | None],
    *,
    keywords: list[str] | None = None,
) -> VetoDecision:
    """증거 텍스트에 치명 키워드가 있는지 검사(부분 문자열, 대소문자 무관)."""
    candidates = keywords if keywords is not None else veto_keywords()
    blob = "\n".join(text for text in texts if text).lower()
    matched = [kw for kw in candidates if kw.lower() in blob]
    return VetoDecision(vetoed=bool(matched), matched_keywords=matched)


class RiskVetoTaskHandler:
    """RISK_VETO 큐 핸들러 — 종목 증거를 스캔해 치명 키워드 시 발행 보류."""

    def __init__(self, connection: Any) -> None:
        from signal_alpha_data_access.repositories import (
            AnalysisRepository,
            NormalizationRepository,
        )

        self._analysis = AnalysisRepository(connection)
        self._normalization = NormalizationRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = _task_context(task.get("task_context"))
        signal_event_ids = _int_list(
            task.get("source_signal_event_ids") or ctx.get("source_signal_event_ids")
        )
        final_signal_id = ctx.get("final_signal_id")

        if not signal_event_ids:
            return {"stock_id": stock_id, "vetoed": False, "skipped_reason": "no_signal_events"}

        rows = [
            dict(row)
            for row in await self._normalization.list_signal_events_by_ids(signal_event_ids)
        ]
        texts: list[str | None] = []
        for row in rows:
            texts.extend([row.get("title"), row.get("summary"), row.get("evidence_text")])

        decision = scan_for_veto(texts)
        applied = False
        if decision.vetoed and final_signal_id is not None:
            await self._analysis.apply_risk_veto(final_signal_id=int(final_signal_id))
            await self._normalization.record_validation_log(
                target_type="final_signal",
                target_id_int=int(final_signal_id),
                validation_type="risk_veto",
                passed=False,
                message="risk_veto: " + ", ".join(decision.matched_keywords),
            )
            applied = True

        return {
            "stock_id": stock_id,
            "final_signal_id": final_signal_id,
            "vetoed": decision.vetoed,
            "matched_keywords": decision.matched_keywords,
            "applied": applied,
        }


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _int_list(value: Any) -> list[int]:
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
