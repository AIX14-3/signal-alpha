"""SYNTHESIZE 큐 핸들러 — 끝단 LLM 종합·설명 + 리스크 리포트(JSON).

RISK_VETO 다음 단계. final_signal(수치/판정) + 최근 meta_signal(결합 변동성) + 소스 근거 +
veto 정보를 모아, LLM이 설정돼 있으면 **설명 내러티브만** 생성하고(수치 불변), 아니면 결정론
폴백으로 내러티브를 만든다. 결과 내러티브는 final_signals(summary/bull/bear)에만 반영하고,
리스크 리포트(JSON)를 반환한다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.orchestrator.queue.context import parse_int_list, parse_task_context
from app.orchestrator.queue.task_types import RISK_VETO
from app.schemas.risk_report import RiskReport
from app.synthesis.synthesizer import RiskNarrative, Synthesizer


class SynthesizeTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        settings: Any = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        from signal_alpha_data_access.repositories import (
            AnalysisRepository,
            MetaSignalRepository,
            NormalizationRepository,
            ProcessingQueueRepository,
        )

        self._analysis = AnalysisRepository(connection)
        self._normalization = NormalizationRepository(connection)
        self._meta = MetaSignalRepository(connection)
        self._queue = ProcessingQueueRepository(connection)
        self._synthesizer = synthesizer if synthesizer is not None else _build_synthesizer(settings)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        final_signal_id = ctx.get("final_signal_id")
        if final_signal_id is None:
            return {"stock_id": stock_id, "skipped_reason": "final_signal_id_required"}

        signal_event_ids = parse_int_list(
            task.get("source_signal_event_ids") or ctx.get("source_signal_event_ids")
        )
        vetoed = bool(ctx.get("vetoed"))
        veto_keywords = [str(k) for k in (ctx.get("matched_keywords") or [])]
        run_key = str(ctx.get("run_key") or "ML")
        stock_code = str(ctx.get("stock_code") or stock_id)

        final_signal = await self._analysis.get_final_signal_by_id(
            final_signal_id=int(final_signal_id)
        )
        if final_signal is None:
            return {"stock_id": stock_id, "skipped_reason": "final_signal_not_found"}
        final_signal = dict(final_signal)

        events = (
            [dict(row) for row in await self._normalization.list_signal_events_by_ids(signal_event_ids)]
            if signal_event_ids
            else []
        )
        evidence = [
            {
                "source_type": event.get("source_type"),
                "title": event.get("title"),
                "summary": event.get("summary"),
                "impact_level": event.get("impact_level"),
                "evidence_url": event.get("evidence_url"),
            }
            for event in events
        ]

        meta_row = await self._meta.latest_for_stock(stock_id=stock_id, run_key=run_key)
        ml_risk = None
        if meta_row is not None:
            meta = dict(meta_row)
            ml_risk = {
                "combined_vol": _to_float(meta.get("combined_vol")),
                "confidence": _to_float(meta.get("confidence")),
                "method": meta.get("method"),
            }

        report = RiskReport(
            stock_id=stock_id,
            stock_code=stock_code,
            signal_date=_to_str(final_signal.get("signal_date")),
            signal=str(final_signal.get("signal") or "neutral"),
            final_score=_to_float(final_signal.get("final_score")),
            confidence=_to_float(final_signal.get("confidence")),
            warning_level=str(final_signal.get("warning_level") or "NORMAL"),
            is_published=bool(final_signal.get("is_published")),
            needs_review=bool(final_signal.get("needs_review")),
            vetoed=vetoed,
            veto_keywords=veto_keywords,
            ml_risk=ml_risk,
            evidence=evidence,
        )

        narrative, source = await self._narrate(report)
        report = _with_narrative(report, narrative, source)

        # final_signals.summary는 LLM이 실제로 더 풍부한 내러티브를 만들었을 때만 갱신한다.
        # 결정론 폴백(deterministic/llm_fallback)으로 AGGREGATE_SIGNAL의 정보성 요약을
        # 일반 문구로 덮어쓰면 기본(LLM off) 배포에서 요약 품질이 저하되므로 보존한다.
        # 리포트(JSON)에는 어느 경우든 내러티브가 담겨 소비자에게 전달된다.
        narrative_persisted = source == "llm"
        if narrative_persisted:
            bull_point, bear_point = _split_bull_bear(
                report.signal, narrative.key_points, narrative.caution_points
            )
            await self._analysis.update_final_signal_narrative(
                final_signal_id=int(final_signal_id),
                summary=narrative.narrative,
                bull_point=bull_point,
                bear_point=bear_point,
            )

        # 선형 체인: 종합 "뒤"에서 리스크 veto가 동작한다(치명 키워드면 정제 루프). 이 종합이
        # 정제(refine) 결과면 refined=true로 알려, 정제 후에도 치명적이면 미발행하게 한다.
        refined = bool(ctx.get("refine"))
        risk_veto_task_id: int | None = None
        if signal_event_ids:
            risk_veto_task_id = await self._queue.enqueue(
                stock_id=stock_id,
                task_type=RISK_VETO,
                priority=str(ctx.get("priority") or "batch"),
                source_signal_event_ids=signal_event_ids,
                task_context={
                    "final_signal_id": int(final_signal_id),
                    "stock_code": stock_code,
                    "run_key": run_key,
                    "refined": refined,
                },
                dedupe=True,
            )

        return {
            "stock_id": stock_id,
            "final_signal_id": int(final_signal_id),
            "narrative_source": source,
            "narrative_persisted": narrative_persisted,
            "vetoed": vetoed,
            "refined": refined,
            "risk_veto_task_id": risk_veto_task_id,
            "report": report.to_dict(),
        }

    async def _narrate(self, report: RiskReport) -> tuple[RiskNarrative, str]:
        if self._synthesizer is not None:
            try:
                narrative = await self._synthesizer.synthesize(_llm_context(report))
                return narrative, "llm"
            except Exception:  # noqa: BLE001 — any LLM/parse/safety failure → deterministic fallback
                return _deterministic_narrative(report), "llm_fallback"
        return _deterministic_narrative(report), "deterministic"


def _llm_context(report: RiskReport) -> dict[str, Any]:
    # LLM에 주는 수치/판정(불변) + 근거. score/direction을 LLM이 못 바꾸게 '설명만' 지시.
    return {
        "stock_code": report.stock_code,
        "signal": report.signal,
        "final_score": report.final_score,
        "confidence": report.confidence,
        "warning_level": report.warning_level,
        "is_published": report.is_published,
        "needs_review": report.needs_review,
        "vetoed": report.vetoed,
        "veto_keywords": report.veto_keywords,
        "ml_risk": report.ml_risk,
        "evidence": report.evidence,
    }


def _deterministic_narrative(report: RiskReport) -> RiskNarrative:
    status = "발행 보류" if (report.vetoed or not report.is_published) else "발행"
    headline = f"{report.signal} 데이터 신호 ({status})"
    body = (
        f"종합 방향은 '{report.signal}', 경보수준 '{report.warning_level}'. "
        f"근거 {len(report.evidence)}건 기반의 데이터 신호입니다."
    )
    key_points = [
        f"[{item.get('source_type')}] {item.get('title')}"
        for item in report.evidence[:3]
        if item.get("title")
    ]
    caution_points: list[str] = []
    if report.vetoed and report.veto_keywords:
        caution_points.append("리스크 veto: " + ", ".join(report.veto_keywords))
    if report.needs_review:
        caution_points.append("검토 필요(needs_review)")
    if not report.is_published:
        caution_points.append("미발행 상태")
    return RiskNarrative(
        headline=headline,
        narrative=body,
        key_points=key_points,
        caution_points=caution_points,
    )


def _with_narrative(report: RiskReport, narrative: RiskNarrative, source: str) -> RiskReport:
    from dataclasses import replace

    return replace(
        report,
        headline=narrative.headline,
        narrative=narrative.narrative,
        key_points=narrative.key_points,
        caution_points=narrative.caution_points,
        narrative_source=source,
    )


def synthesis_llm_enabled(settings: Any) -> bool:
    """끝단 종합 LLM이 실제로 구성돼 있는지(=``_build_synthesizer`` 가 클라이언트를 만들 수 있는지).

    ``_build_synthesizer`` 와 **동일 조건**만 평가하되 클라이언트를 만들지 않는다(부작용 없음).
    RISK_VETO가 'LLM 정제 루프를 돌릴 가치가 있는지' 판단에 쓴다 — LLM이 없으면 정제는 무동작이라
    정제 왕복 없이 곧장 발행 보류하는 게 맞다(``gates/risk_veto.py`` 참고).
    """
    if settings is None:
        return False
    if str(os.getenv("SYNTHESIS_USE_LLM") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if not os.getenv("SYNTHESIS_LLM_MODEL"):
        return False
    provider = (os.getenv("SYNTHESIS_LLM_PROVIDER") or "gemini").strip().lower()
    if provider == "gemini":
        return bool(getattr(settings, "gemini_api_key", None))
    if provider == "openai":
        return bool(getattr(settings, "openai_api_key", None))
    return False


def _build_synthesizer(settings: Any) -> Synthesizer | None:
    """env(SYNTHESIS_*) + settings api 키로 LLM 종합기를 만든다. 미설정 시 None(결정론 폴백)."""
    if not synthesis_llm_enabled(settings):
        return None
    model = os.getenv("SYNTHESIS_LLM_MODEL")
    provider = (os.getenv("SYNTHESIS_LLM_PROVIDER") or "gemini").strip().lower()
    timeout = float(os.getenv("SYNTHESIS_LLM_TIMEOUT_SECONDS") or 20.0)

    from app.analyzers.dart.llm import GeminiGenerateContentClient, OpenAiChatClient
    from app.observability.langsmith import maybe_trace

    # 엔드포인트 기본값은 dart/llm.py 클라이언트가 자체 보유한다. settings가 명시한 경우에만
    # base_url을 넘겨, 공개 엔드포인트 URL이 두 곳에 중복되지 않게 한다.
    if provider == "gemini" and getattr(settings, "gemini_api_key", None):
        gemini_base = getattr(settings, "gemini_base_url", None)
        client: Any = GeminiGenerateContentClient(
            api_key=settings.gemini_api_key,
            **({"base_url": gemini_base} if gemini_base else {}),
        )
    elif provider == "openai" and getattr(settings, "openai_api_key", None):
        openai_base = getattr(settings, "openai_base_url", None)
        client = OpenAiChatClient(
            api_key=settings.openai_api_key,
            **({"base_url": openai_base} if openai_base else {}),
        )
    else:
        return None
    # LangSmith 관측(켜져 있을 때만; 아니면 동일 클라이언트 반환).
    return Synthesizer(client=maybe_trace(client, name="synthesis"), model=model, timeout_seconds=timeout)


def _split_bull_bear(
    signal: str,
    key_points: list[str],
    caution_points: list[str],
) -> tuple[str | None, str | None]:
    """내러티브 포인트를 신호 방향에 맞춰 bull/bear 컬럼으로 매핑.

    ``caution_points``(주의/리스크)는 항상 bear 쪽. ``key_points``(핵심 근거)는 방향에 따라:
    negative 신호면 핵심 근거가 곧 약세 근거이므로 bear 로, 그 외(positive/neutral/mixed)는
    bull 로 둔다. 기존엔 방향과 무관하게 key→bull 고정이라 negative 신호의 약세 근거가
    bull_point 에 들어가는 의미 불일치가 있었다.
    """
    direction = (signal or "").lower()
    if "negative" in direction:
        bull: list[str] = []
        bear = [*key_points, *caution_points]
    else:
        bull = list(key_points)
        bear = list(caution_points)
    return ("; ".join(bull) or None, "; ".join(bear) or None)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
