"""SYNTHESIZE 큐 핸들러 — 끝단 LLM 종합·설명 + 리스크 리포트(JSON).

발행 직전 단계. final_signal(수치/판정) + 소스 근거를 모아, LLM이 설정돼 있으면 **설명
내러티브만** 생성하고(수치 불변), 아니면 결정론 폴백으로 내러티브를 만든다. 유일 가드는
synthesizer 의 법적 금지단어 필터(``_reject_investment_advice``)뿐 — 위반 시 결정론 폴백.
결과 내러티브는 final_signals(summary/bull/bear)에 반영하고, 곧장 PUBLISH_SIGNALS 를 인큐해
백엔드로 무조건 발행한다(발행 차단 게이트 폐기).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any

from app.orchestrator.queue.context import (
    enqueue_publish_signals,
    parse_int_list,
    parse_task_context,
)
from app.schemas.risk_report import RiskReport
from app.synthesis.synthesizer import RiskNarrative, Synthesizer

logger = logging.getLogger("synthesize")


class SynthesizeTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        settings: Any = None,
        synthesizer: Synthesizer | None = None,
        audit_agent: Any = None,
    ) -> None:
        from signal_alpha_data_access.repositories import (
            AnalysisRepository,
            NormalizationRepository,
            ProcessingQueueRepository,
        )

        self._analysis = AnalysisRepository(connection)
        self._normalization = NormalizationRepository(connection)
        self._queue = ProcessingQueueRepository(connection)
        self._settings = settings
        self._synthesizer = synthesizer if synthesizer is not None else _build_synthesizer(settings)
        # 테스트용 DI(선택). None 이면 프로덕션 경로에서 _build_audit_agent 로 빌드. 기존 synthesizer DI와 동형.
        self._audit_agent = audit_agent

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        ctx = parse_task_context(task.get("task_context"))
        final_signal_id = ctx.get("final_signal_id")
        if final_signal_id is None:
            return {"stock_id": stock_id, "skipped_reason": "final_signal_id_required"}

        signal_event_ids = parse_int_list(
            task.get("source_signal_event_ids") or ctx.get("source_signal_event_ids")
        )
        stock_code = str(ctx.get("stock_code") or stock_id)

        final_signal = await self._analysis.get_final_signal_by_id(
            final_signal_id=int(final_signal_id)
        )
        if final_signal is None:
            return {"stock_id": stock_id, "skipped_reason": "final_signal_not_found"}
        final_signal = dict(final_signal)
        # 주가(PRICE)만의 독립 예측을 score_breakdown에서 분리 — 대체데이터와 합치기 전의 값.
        # 사용자에게 따로 노출하고, LLM이 대체데이터 근거와 "합쳐" 설명할 입력으로 쓴다.
        price_prediction = _price_prediction(final_signal.get("score_breakdown"))
        # REPORT 결정론 밸류에이션 facts(목표주가/투자의견 등) — 점수엔 안 들어가고, 끝단 LLM이
        # DART 공시(evidence)와 함께 "이 점수가 나온 이유"의 근거로 정제·서술한다(소스별 라우팅).
        report_valuation = _report_valuation(final_signal.get("score_breakdown"))
        # 소스별 6 + 통합 1 = 7개 예측률(주가 BASE ⊕ 대체데이터). RETURN_COMBINE 이 final_signals 에
        # 적재한 값(P3) → web 표시값과 동일. LLM 은 이 수치를 바꾸지 않고 설명만 한다(C안 P4).
        source_predictions = _loads_breakdown(final_signal.get("source_predictions"))
        # last-known 재사용 신선도 — score_breakdown 의 data_age_days>0 인 소스(직전 분석 재사용).
        # LLM 이 "최종 업데이트 N일 전" 으로 서술하는 근거(수치 불변, 설명만).
        source_freshness = _source_freshness(final_signal.get("score_breakdown"))

        # 소스별 LLM 서술 라인(독립) — 파싱데이터 + 예측률을 묶어 읽기 쉬운 한국어 서술을
        # score_breakdown.{SRC}.summary 에 병합한다(수치 불변). 플래그 OFF/실패 시 기존 요약 유지.
        await self._narrate_sources(
            stock_id=stock_id,
            stock_code=stock_code,
            signal_date=final_signal.get("signal_date"),
            source_predictions=source_predictions,
            price_prediction=price_prediction,
            report_valuation=report_valuation,
            score_breakdown=_loads_breakdown(final_signal.get("score_breakdown")),
        )

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

        # vol(변동성) ML 채널은 폐기됨(#585) — combined_vol 은 더 이상 적재되지 않으므로 ml_risk 는 None.
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
            vetoed=False,
            veto_keywords=[],
            ml_risk=None,
            price_prediction=price_prediction,
            report_valuation=report_valuation,
            source_predictions=source_predictions,
            source_freshness=source_freshness,
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

        # 발행물 감사(감사 agent) — LLM 서술일 때만, 발행 직전에 규제·사실 grounding 을 독립 감사한다.
        # flag-only · non-blocking: 미구성/실패/degrade 는 모두 None(로그만) → 아래 발행은 그대로 진행.
        publication_audit = await self._audit_publication(
            report=report,
            narrative=narrative,
            source=source,
            score_breakdown=_loads_breakdown(final_signal.get("score_breakdown")),
        )

        # 선형 체인: 종합 결과를 곧장 백엔드로 무조건 발행한다(발행 차단 게이트 폐기). 법적 금지단어
        # 필터는 synthesizer 단계에서 이미 적용됐다(위반 시 결정론 폴백 서술). 발행 우선순위 전파.
        priority = str(ctx.get("priority") or "batch")
        publish_task_id = await enqueue_publish_signals(
            self._queue,
            settings=self._settings,
            stock_id=stock_id,
            stock_code=stock_code,
            priority=priority,
        )

        return {
            "stock_id": stock_id,
            "final_signal_id": int(final_signal_id),
            "narrative_source": source,
            "narrative_persisted": narrative_persisted,
            "publish_task_id": publish_task_id,
            "publication_audit": publication_audit,
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

    async def _audit_publication(
        self,
        *,
        report: RiskReport,
        narrative: RiskNarrative,
        source: str,
        score_breakdown: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """발행물 규제·사실 grounding 감사(감사 agent). **LLM 서술일 때만** 동작.

        flag-only · non-blocking: 미구성/키없음/실패/degrade 는 모두 None 반환(로그만) → 호출측 발행은 계속.
        점수/방향/서술 불변(감사는 verdict 만 반환, 파이프에 안 씀). needs_review 미접근.
        게이트: ``source == "llm"`` + (env ``PUBLICATION_AUDIT_ENABLED`` truthy 또는 테스트용 ``audit_agent`` 주입).
        """
        if source != "llm":
            return None
        enabled = str(os.getenv("PUBLICATION_AUDIT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
        if self._audit_agent is None and not enabled:
            return None
        try:
            agent = self._audit_agent or self._build_audit_agent(report)
            if agent is None:  # GEMINI 키 없음/미구성 → skip(발행 계속)
                return None
            audited_text = "\n".join(
                [narrative.narrative, *narrative.key_points, *narrative.caution_points]
            ).strip()
            verdict = await agent.audit(
                narrative=audited_text, evidence={"score_breakdown": score_breakdown}
            )
            logger.info(
                "pub-audit stock=%s compliant=%s grounding_ok=%s flags=%d hops=%d degraded=%s",
                report.stock_id, verdict.compliant, verdict.grounding_ok,
                len(verdict.flags), verdict.hops, verdict.degraded,
            )
            for flag in verdict.flags:
                logger.info("  pub-audit flag [%s] %s", flag.type, flag.reason)
            return {
                "compliant": verdict.compliant,
                "grounding_ok": verdict.grounding_ok,
                "flags": [
                    {"span": f.span, "type": f.type, "reason": f.reason, "evidence": f.evidence}
                    for f in verdict.flags
                ],
                "trace": verdict.trace,
                "hops": verdict.hops,
                "degraded": verdict.degraded,
                "model": verdict.model,
                "prompt_ver": verdict.prompt_ver,
            }
        except Exception as exc:  # noqa: BLE001 — 감사 실패는 격리, 발행 절대 안 막음
            logger.warning("pub-audit 실패 격리 (stock=%s): %s", report.stock_id, exc)
            return None

    def _build_audit_agent(self, report: RiskReport) -> Any | None:
        """프로덕션 감사 agent 빌드 — GEMINI 키 없거나 미구성이면 None(→ 감사 skip).

        evidence_provider = 핸들러가 이미 로드한 추가 컨텍스트를 read-only 서빙(교정 A2, 새 DB 접근 0):
        insufficient 시 score_breakdown 재서빙이 아니라 source_predictions/freshness 등으로 보강.
        """
        from app.audit.agent import PublicationAuditAgent
        from app.clients.gemini_client import GeminiError, GeminiJsonClient

        api_key = getattr(self._settings, "gemini_api_key", None) or os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.info("pub-audit: GEMINI 키 없음 — 감사 skip")
            return None
        try:
            client = GeminiJsonClient(
                api_key=api_key,
                model=os.getenv("AUDIT_LLM_MODEL") or "gemini-2.5-flash",
                temperature=0.0,  # 판정 일관성(§8)
            )
        except GeminiError as exc:
            logger.info("pub-audit: LLM 미구성 (%s) — 감사 skip", exc)
            return None

        extras = {
            "source_predictions": report.source_predictions,
            "source_freshness": report.source_freshness,
            "price_prediction": report.price_prediction,
            "report_valuation": report.report_valuation,
            "final_score": report.final_score,
            "confidence": report.confidence,
            "warning_level": report.warning_level,
        }

        async def _provider(_claim: Any, _need: Any) -> dict[str, Any]:
            return extras

        return PublicationAuditAgent(client=client, evidence_provider=_provider)

    async def _narrate_sources(
        self,
        *,
        stock_id: int,
        stock_code: str,
        signal_date: Any,
        source_predictions: dict[str, Any] | None,
        price_prediction: dict[str, Any] | None = None,
        report_valuation: dict[str, Any] | None = None,
        score_breakdown: dict[str, Any] | None = None,
    ) -> None:
        """소스별 독립 LLM 서술 라인(DART/PRICE/REPORT). 각 소스는 플래그로 개별 게이팅하며,
        실패해도 발행을 막지 않는다(기존 요약 유지). 이미 ``narrated`` 인 소스는 LLM 재호출 없이
        skip(멱등) — AGGREGATE 가 score_breakdown 을 재생성하면 플래그가 사라져 자동 재서술."""
        if self._settings is None:
            return
        if _source_narrate_enabled(self._settings, "DART") and not _already_narrated(score_breakdown, "DART"):
            try:
                await self._narrate_dart(stock_id, stock_code, signal_date, source_predictions)
            except Exception:  # noqa: BLE001 — 서술 실패가 발행을 막지 않음
                logger.exception("DART narrate 실패 — 기존 요약 유지 (stock_id=%s)", stock_id)
        if _source_narrate_enabled(self._settings, "PRICE") and not _already_narrated(score_breakdown, "PRICE"):
            try:
                await self._narrate_price(stock_id, stock_code, price_prediction, source_predictions)
            except Exception:  # noqa: BLE001
                logger.exception("PRICE narrate 실패 — 기존 요약 유지 (stock_id=%s)", stock_id)
        if _source_narrate_enabled(self._settings, "REPORT") and not _already_narrated(score_breakdown, "REPORT"):
            try:
                await self._narrate_report(
                    stock_id, stock_code, signal_date, report_valuation, source_predictions
                )
            except Exception:  # noqa: BLE001
                logger.exception("REPORT narrate 실패 — 기존 요약 유지 (stock_id=%s)", stock_id)

    def _narrate_client(self, source_key: str) -> tuple[Any, str, float] | None:
        """소스별 LLM 클라이언트/모델/타임아웃 — 키 없으면 None. ({SRC}_LLM_PROVIDER/MODEL env)."""
        from app.narrate.base import build_narrate_client

        provider = (os.getenv(f"{source_key}_LLM_PROVIDER") or "gemini").strip().lower()
        model = str(os.getenv(f"{source_key}_LLM_MODEL") or "")
        if not model:
            return None
        client = build_narrate_client(provider, settings=self._settings)
        if client is None:
            return None
        timeout = float(os.getenv(f"{source_key}_LLM_TIMEOUT_SECONDS") or 30.0)
        return client, model, timeout

    async def _narrate_price(
        self,
        stock_id: int,
        stock_code: str,
        price_prediction: dict[str, Any] | None,
        source_predictions: dict[str, Any] | None,
    ) -> None:
        from app.narrate.price import PriceNarrator

        built = self._narrate_client("PRICE")
        if built is None:
            return
        client, model, timeout = built
        pred = (source_predictions or {}).get("SRC_PRICE")
        narrator = PriceNarrator(client=client, model=model, timeout_seconds=timeout)
        narrative = await narrator.narrate(
            stock_code=stock_code, analysis=price_prediction, prediction_rate=pred
        )
        await self._analysis.update_source_narrative(
            stock_id=stock_id, source="PRICE",
            summary=narrative.summary, narrative_points=narrative.key_facts,
        )
        logger.info("PRICE narrate 적용 stock_id=%s facts=%d", stock_id, len(narrative.key_facts))

    async def _narrate_report(
        self,
        stock_id: int,
        stock_code: str,
        signal_date: Any,
        report_valuation: dict[str, Any] | None,
        source_predictions: dict[str, Any] | None,
    ) -> None:
        from app.narrate.report import ReportNarrator

        built = self._narrate_client("REPORT")
        if built is None:
            return
        client, model, timeout = built
        rows = await self._normalization.list_recent_source_events(
            stock_id=stock_id, source_type="REPORT", as_of=signal_date, limit=12
        )
        events = [dict(r) for r in rows]
        if not report_valuation and not events:
            return  # REPORT 원천 미적재 — no-op
        pred = (source_predictions or {}).get("SRC_REPORT")
        narrator = ReportNarrator(client=client, model=model, timeout_seconds=timeout)
        narrative = await narrator.narrate(
            stock_code=stock_code, valuation=report_valuation,
            prediction_rate=pred, events=events,
        )
        await self._analysis.update_source_narrative(
            stock_id=stock_id, source="REPORT",
            summary=narrative.summary, narrative_points=narrative.key_facts,
        )
        if events:
            await self._analysis.attach_evidence_events(
                stock_id=stock_id,
                event_ids=[int(e["id"]) for e in events if e.get("id") is not None],
            )
        logger.info("REPORT narrate 적용 stock_id=%s facts=%d", stock_id, len(narrative.key_facts))

    async def _narrate_dart(
        self,
        stock_id: int,
        stock_code: str,
        signal_date: Any,
        source_predictions: dict[str, Any] | None,
    ) -> None:
        from app.narrate.dart import DartNarrator, select_narrate_events

        built = self._narrate_client("DART")
        if built is None:
            return
        client, model, timeout = built
        rows = await self._normalization.list_recent_source_events(
            stock_id=stock_id, source_type="DART", as_of=signal_date, limit=20
        )
        events = [dict(row) for row in rows]
        if not events:
            return
        picked = select_narrate_events(events, limit=12)
        pred = (source_predictions or {}).get("SRC_DART")
        narrator = DartNarrator(
            client=client,
            model=model,
            timeout_seconds=timeout,
        )
        narrative = await narrator.narrate(
            stock_code=stock_code, events=picked, prediction_rate=pred
        )
        await self._analysis.update_source_narrative(
            stock_id=stock_id,
            source="DART",
            summary=narrative.summary,
            narrative_points=narrative.key_facts,
        )
        # 근거(공시 목록) 노출 — 서술에 쓴 DART 이벤트를 현재 신호의 분석결과 근거에 합집합 추가.
        await self._analysis.attach_evidence_events(
            stock_id=stock_id, event_ids=[int(e["id"]) for e in picked if e.get("id") is not None]
        )
        logger.info(
            "DART narrate 적용 stock_id=%s events=%d facts=%d",
            stock_id,
            len(picked),
            len(narrative.key_facts),
        )


def _already_narrated(score_breakdown: dict[str, Any] | None, source: str) -> bool:
    """해당 소스가 이미 서술됨(narrated=True) — 재SYNTHESIZE 시 LLM 재호출 skip(멱등).
    AGGREGATE 가 score_breakdown 을 재생성하면 narrated 가 사라져 다음 사이클에 자동 재서술된다."""
    if not isinstance(score_breakdown, dict):
        return False
    entry = score_breakdown.get(source)
    return isinstance(entry, dict) and bool(entry.get("narrated"))


def _source_narrate_enabled(settings: Any, source_key: str) -> bool:
    """소스별 서술 LLM 구성 여부 — {SRC}_USE_LLM + {SRC}_LLM_MODEL + provider 키. (부작용 없는 게이트.)"""
    if settings is None:
        return False
    if str(os.getenv(f"{source_key}_USE_LLM") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if not os.getenv(f"{source_key}_LLM_MODEL"):
        return False
    provider = (os.getenv(f"{source_key}_LLM_PROVIDER") or "gemini").strip().lower()
    if provider == "gemini":
        return bool(getattr(settings, "gemini_api_key", None))
    if provider == "openai":
        return bool(getattr(settings, "openai_api_key", None))
    return False


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
        # 주가 단독 예측 — LLM이 대체데이터 근거와 합쳐 설명하되, 이 예측 자체는 바꾸지 않는다.
        "price_prediction": report.price_prediction,
        # REPORT 밸류에이션 facts — LLM이 근거로 정제·서술(점수 변경 금지).
        "report_valuation": report.report_valuation,
        # 7개 예측률(주가 BASE ⊕ 대체데이터) — 있을 때만 컨텍스트에 포함(없으면 기존과 동일).
        **({"source_predictions": report.source_predictions} if report.source_predictions else {}),
        # last-known 재사용 신선도 — 재사용된 소스가 있을 때만(LLM 이 "최종 업데이트 N일 전" 서술).
        **({"source_freshness": report.source_freshness} if report.source_freshness else {}),
        "evidence": report.evidence,
    }


def _source_freshness(score_breakdown: Any) -> dict[str, int]:
    """score_breakdown 에서 직전 분석을 재사용한 소스의 나이(일)를 추출.

    ``data_age_days>0`` (그날 갱신 없이 유효기간 내 직전 결과 재사용)이고 데이터가 있는
    (missing 아님) 소스만 ``{source: age_days}`` 로 반환. LLM/리포트가 "최종 업데이트 N일 전"
    표기에 쓴다. 모두 당일(0)이면 빈 dict.
    """
    breakdown = _loads_breakdown(score_breakdown)
    if breakdown is None:
        return {}
    freshness: dict[str, int] = {}
    for source, detail in breakdown.items():
        if not isinstance(detail, dict):
            continue
        if str(detail.get("data_status") or "") in {"missing", "failed"}:
            continue
        age = detail.get("data_age_days")
        try:
            age_int = int(age)
        except (TypeError, ValueError):
            continue
        if age_int > 0:
            freshness[str(source)] = age_int
    return freshness


def _report_valuation(score_breakdown: Any) -> dict[str, Any] | None:
    """score_breakdown 에서 REPORT 소스의 결정론 밸류에이션 facts 를 분리해 반환.

    REPORT 는 features-only(no_signal)라 점수엔 안 들어가지만, 목표주가/투자의견 등 정형
    valuation 은 LLM 종합의 근거로 가치가 크다. 없으면 None.
    """
    breakdown = _loads_breakdown(score_breakdown)
    if breakdown is None:
        return None
    report = breakdown.get("REPORT")
    if not isinstance(report, dict):
        return None
    valuation = report.get("valuation")
    return dict(valuation) if isinstance(valuation, dict) else None


def _loads_breakdown(score_breakdown: Any) -> dict[str, Any] | None:
    """score_breakdown(JSONB dict 또는 JSON 문자열)을 dict 로 정규화. 실패 시 None."""
    breakdown = score_breakdown
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except (TypeError, ValueError):
            return None
    return breakdown if isinstance(breakdown, dict) else None


def _price_prediction(score_breakdown: Any) -> dict[str, Any] | None:
    """final_signals.score_breakdown 에서 PRICE 소스 항목만 분리해 주가 단독 예측으로 반환.

    score_breakdown 은 JSONB(dict) 또는 JSON 문자열일 수 있다. PRICE 항목이 없거나
    데이터가 없으면(missing) None. score_100 을 예측확률 proxy(0~100)로 노출한다.
    """
    breakdown = _loads_breakdown(score_breakdown)
    if breakdown is None:
        return None
    price = breakdown.get("PRICE")
    if not isinstance(price, dict):
        return None
    if str(price.get("data_status") or "") in {"missing", "failed"}:
        return None
    return {
        "direction": price.get("direction"),
        "score_100": price.get("score_100"),
        "score": price.get("score"),
        "data_status": price.get("data_status"),
        "summary": price.get("summary"),
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
    # 주가 단독 예측을 별도 라인으로 노출(대체데이터 종합과 구분).
    if report.price_prediction:
        pp = report.price_prediction
        key_points.insert(
            0,
            f"[주가예측] 방향 {pp.get('direction')}, 예측확률 {pp.get('score_100')}",
        )
    # 7개 예측률(주가 BASE ⊕ 대체데이터) 통합치를 한 줄로 노출(수치 불변).
    if report.source_predictions:
        integrated = report.source_predictions.get("SRC") or {}
        if integrated.get("final_score") is not None:
            key_points.append(
                f"[메타예측] 통합 방향 {integrated.get('direction')} · "
                f"소스별 {len(report.source_predictions)}개 예측률"
            )
    caution_points: list[str] = []
    if report.vetoed and report.veto_keywords:
        caution_points.append("리스크 veto: " + ", ".join(report.veto_keywords))
    # last-known 재사용 소스의 신선도를 "최종 업데이트 N일 전" 으로 안내.
    if report.source_freshness:
        stale = ", ".join(
            f"{source} {age}일 전" for source, age in sorted(report.source_freshness.items())
        )
        caution_points.append(f"일부 데이터 재사용(최종 업데이트): {stale}")
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
