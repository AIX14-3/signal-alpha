"""리스크 veto — architecture.mermaid의 "리스크 veto (치명 키워드 차단)".

게이트2(=AGGREGATE_SIGNAL) 통과로 발행된 신호라도, 종목의 증거 텍스트에 치명 키워드
(상장폐지·감사의견거절·횡령 등)가 있으면 발행을 보류한다. 결정론적 단계로, 점수/방향은
그대로 두고 ``final_signals`` 의 발행 플래그만 차단(is_published=FALSE, needs_review=TRUE,
warning_level=WARNING)하고 사유를 ``validation_logs`` 에 남긴다.

``scan_for_veto`` 는 DB 미접근 순수 함수(단위 테스트 용이). ``RiskVetoTaskHandler`` 는 큐의
``RISK_VETO`` 핸들러로, AGGREGATE_SIGNAL이 발행 신호에 대해 enqueue 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from dataclasses import dataclass

from app.gates.rules.veto_keywords import veto_keywords
from app.orchestrator.queue.context import parse_int_list, parse_task_context
from app.orchestrator.queue.task_types import SYNTHESIZE

# 강한 해소/부인 신호만(보수적). 키워드 직후에 이런 표현이 있으면 치명 사건이 *해소/부인*된
# 맥락("상장폐지 우려 해소", "횡령 혐의 무혐의")으로 보고 그 등장은 veto로 치지 않는다.
# risk veto는 false negative(위험 신호 발행)가 false positive(정상 신호 보류)보다 위험하므로,
# "없"/"아니" 같은 약한 일반 부정어는 일부러 넣지 않는다(recall 보존).
_NEGATION_CUES: tuple[str, ...] = (
    "해소",
    "무혐의",
    "기각",
    "각하",
    "사실무근",
    "오보",
    "루머",
    "철회",
    "혐의를 벗",
    "혐의 없",
    "혐의가 없",
    "사실이 아니",
    "사실 아니",
)
# 키워드 등장 직후 해소어를 살펴볼 문자 수.
_NEGATION_WINDOW = 30


@dataclass(frozen=True)
class VetoDecision:
    vetoed: bool
    matched_keywords: list[str]


def _has_unnegated_occurrence(blob: str, keyword: str) -> bool:
    """``keyword`` 가 해소/부인 맥락 밖에서 한 번이라도 등장하면 True.

    각 등장 위치 직후 ``_NEGATION_WINDOW`` 글자 안에 해소어가 있으면 그 등장은 무시하고,
    비부정 등장이 하나라도 있으면 진짜 veto 매치로 본다.
    """
    start = blob.find(keyword)
    while start != -1:
        end = start + len(keyword)
        window = blob[start : end + _NEGATION_WINDOW]
        if not any(cue in window for cue in _NEGATION_CUES):
            return True
        start = blob.find(keyword, end)
    return False


def scan_for_veto(
    texts: Iterable[str | None],
    *,
    keywords: list[str] | None = None,
) -> VetoDecision:
    """증거 텍스트에 치명 키워드가 있는지 검사(부분 문자열, 대소문자 무관).

    단순 부분 문자열은 "상장폐지 우려 해소"·"횡령 혐의 무혐의"처럼 사건이 *해소/부인*된 문장도
    걸어 정상 신호를 잘못 보류시킨다. 그래서 키워드 직후의 **강한 해소/부인 신호**(``_NEGATION_CUES``)
    가 있는 등장은 제외하고, 비부정 등장이 1건 이상인 키워드만 matched 로 본다. 보수적으로 강한
    해소어만 제외해 진짜 치명 사건은 그대로 veto 한다(false negative 회피).
    """
    candidates = keywords if keywords is not None else veto_keywords()
    blob = "\n".join(text for text in texts if text).lower()
    matched = [kw for kw in candidates if _has_unnegated_occurrence(blob, kw.lower())]
    return VetoDecision(vetoed=bool(matched), matched_keywords=matched)


class RiskVetoTaskHandler:
    """RISK_VETO 큐 핸들러 — 종목 증거를 스캔해 치명 키워드 시 발행 보류."""

    def __init__(self, connection: Any, *, settings: Any = None) -> None:
        from signal_alpha_data_access.repositories import (
            AnalysisRepository,
            NormalizationRepository,
            ProcessingQueueRepository,
        )

        self._analysis = AnalysisRepository(connection)
        self._normalization = NormalizationRepository(connection)
        self._queue = ProcessingQueueRepository(connection)
        self._settings = settings

    def _llm_refine_available(self) -> bool:
        """끝단 종합 LLM이 구성돼 있어 정제 루프가 의미가 있는지.

        LLM이 없으면 정제 패스의 SYNTHESIZE는 신호를 바꾸지 못하고(결정론 폴백) RISK_VETO만 한 번
        더 돌다 결국 같은 보류로 끝난다. 그 무의미한 왕복을 피하려 LLM 미구성 시 정제를 건너뛴다.
        """
        from app.synthesis.tasks import synthesis_llm_enabled

        settings = self._settings
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        return synthesis_llm_enabled(settings)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        signal_event_ids = parse_int_list(
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

        # 검사 대상에 LLM 종합 텍스트도 포함한다(요구사항: 데이터 veto_keywords + LLM 종합 텍스트).
        if final_signal_id is not None:
            fs = await self._analysis.get_final_signal_by_id(final_signal_id=int(final_signal_id))
            if fs is not None:
                fs = dict(fs)
                texts.extend([fs.get("summary"), fs.get("bull_point"), fs.get("bear_point")])

        decision = scan_for_veto(texts)
        refined = bool(ctx.get("refined"))

        # 리스크 veto는 LLM 종합 "뒤"에서 동작한다. 치명 키워드가 나와도 미발행으로 버리지 않고
        # LLM 정제(리스크 강조)를 1회 거친다. 정제 후에도 치명적이면 그때 발행 보류(needs_review).
        # 단, LLM이 구성돼 있지 않으면 정제는 무동작이므로 정제 왕복 없이 곧장 보류한다.
        applied = False
        synthesize_task_id: int | None = None
        if decision.vetoed and final_signal_id is not None:
            do_refine = not refined and self._llm_refine_available()
            if do_refine:
                synthesize_task_id = await self._queue.enqueue(
                    stock_id=stock_id,
                    task_type=SYNTHESIZE,
                    priority=str(ctx.get("priority") or "batch"),
                    source_signal_event_ids=signal_event_ids,
                    task_context={
                        "final_signal_id": int(final_signal_id),
                        "stock_code": ctx.get("stock_code"),
                        "run_key": ctx.get("run_key") or "ML",
                        "refine": True,
                        "vetoed": True,
                        "matched_keywords": decision.matched_keywords,
                    },
                    dedupe=True,
                )
            else:
                reason = "정제 후에도 치명" if refined else "LLM 미구성 → 정제 생략"
                await self._analysis.apply_risk_veto(final_signal_id=int(final_signal_id))
                await self._normalization.record_validation_log(
                    target_type="final_signal",
                    target_id_int=int(final_signal_id),
                    validation_type="risk_veto",
                    passed=False,
                    message=f"risk_veto({reason}): " + ", ".join(decision.matched_keywords),
                )
                applied = True

        return {
            "stock_id": stock_id,
            "final_signal_id": final_signal_id,
            "vetoed": decision.vetoed,
            "matched_keywords": decision.matched_keywords,
            "refined": refined,
            "applied": applied,
            "synthesize_task_id": synthesize_task_id,
        }
