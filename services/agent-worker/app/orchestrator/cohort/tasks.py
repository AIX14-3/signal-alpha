"""SCORE_COHORT 태스크 핸들러 — 코호트 N종목 × 1소스를 LLM 호출 1회로 채점해 발행.

⚠️ 이 핸들러는 "숫자는 결정론이 소유, LLM 은 근거만" 불변식의 **의도적 폐기**다
(2026-07-13 사용자 승인). ``LLM_SCORING_ENABLED`` + ``LLM_SCORING_SOURCES`` 로 켠다.

## 입도 (granularity)
기존 ANALYZE_{SOURCE} 는 종목 1개/태스크지만, 코호트 상대 채점(여러 종목을 한
프롬프트에 넣어 서로 비교)이 점수 일관성의 주 방어수단이자 비용 1/N 의 원천이라
**배치 태스크**로 앉힌다. ``stock_id=NULL`` + ``task_context={source, as_of,
tickers[]}`` — RECORD_EPISODE_OUTCOMES 가 이미 쓰는 배치 선례라 스키마 변경 없음.

## 저장 계약
종목별 ``SourceResult`` 로 매핑해 **기존 persistence 를 그대로** 태운다
(``build_source_signal`` → ``AlternativeSignalPersistence.save(run_key=소스 고유
키)``). 집계 fan-in(``list_latest_source_results_for_stock``)은 run_key LIKE
프리픽스로 소스별 최신 1행을 고르므로 무변경으로 이 결과를 집어간다.

## 실패 폴백 (LLM_SCORING_FALLBACK)
- ``rules``: 결정론 채점기(``app/backtest/reference_scorer.score_source`` — 수식
  제거(D) 후에도 이 파일이 본체를 보존한다)로 폴백해 발행 공백을 막는다.
  ``analysis_source="rules_fallback"`` + ``llm_error`` 로 관측 가능.
- ``no_signal``: 그날 그 소스는 아무것도 쓰지 않는다 — 오늘 no_signal 행을 쓰면
  fan-in 의 DISTINCT ON 최신행이 어제의 정상 점수를 **가려버리므로**, 안 쓰는 쪽이
  last-known 재사용(어제 LLM 점수 승계)을 살린다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Mapping

from app.analyzers.cohort import (
    COHORT_SOURCES,
    CohortSourceSpec,
    build_attention,
    build_evidence,
    to_source_result,
)
from app.analyzers.config import AggregatorConfig, AnalyzerRuntimeConfig
from app.analyzers.llm_scorer import StockContext, StockScore, score_cohort
from app.aggregator.per_source import build_source_signal
from app.core.config import Settings, get_settings
from app.ml.source_features import pit_rows
from app.orchestrator.alternative_persistence import AlternativeSignalPersistence
from app.orchestrator.queue.context import enqueue_aggregate
from app.schemas.source_result import SourceResult
from signal_alpha_data_access.repositories import (
    ProcessingQueueRepository,
    RawDetailRepository,
)

logger = logging.getLogger(__name__)


class _CohortRegistration:
    """persistence 가 요구하는 최소 계약(.source/.debate_method)만 채우는 경량 등록.

    ``SourceRegistration`` 은 순수 Analyzer 구현을 요구해 DART/REPORT 에 못 쓴다 —
    ``AlternativeSignalPersistence`` 는 이 두 속성만 읽는다."""

    def __init__(self, spec: CohortSourceSpec) -> None:
        self.source = spec.source
        self.debate_method = spec.debate_method


class CohortScoreTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        settings: Settings | None = None,
        client_factory: Any | None = None,
        aggregator_config: AggregatorConfig | None = None,
        runtime_config: AnalyzerRuntimeConfig | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings or get_settings()
        self._client_factory = client_factory
        self._aggregator_config = aggregator_config or AggregatorConfig.from_env()
        self._runtime = runtime_config or AnalyzerRuntimeConfig.from_env()
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        ctx = _task_context(task.get("task_context"))
        source = str(ctx.get("source") or "").upper()
        spec = COHORT_SOURCES.get(source)
        if spec is None:
            raise ValueError(f"SCORE_COHORT: unknown source {source!r}")
        as_of = _to_date(ctx.get("as_of")) if ctx.get("as_of") else date.today()
        tickers = [str(t) for t in (ctx.get("tickers") or [])]
        if not tickers:
            return {"source": source, "status": "empty", "reason": "no tickers"}

        stocks = await self._resolve_stocks(tickers)
        repository = RawDetailRepository(self._connection)
        loader = spec.build_loader(repository=repository, connection=self._connection)

        cohort: list[StockContext] = []
        pit_by_ticker: dict[str, list[dict]] = {}
        close_by_ticker: dict[str, float | None] = {}
        no_data: list[str] = []
        for stock_id, ticker, name in stocks:
            try:
                ev_rows = await loader.load(stock_id=stock_id, stock_code=ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — 한 종목 로드 실패가 코호트를 못 가라앉힌다
                logger.warning("SCORE_COHORT %s: %s 로드 실패: %s", source, ticker, exc)
                no_data.append(ticker)
                continue
            meta = ev_rows[0].metadata if ev_rows else {}
            raw = list(meta.get(spec.metadata_key) or meta.get("rows") or [])
            pit = pit_rows(raw, as_of, date_key=spec.date_key)
            if not pit:
                no_data.append(ticker)
                continue
            close = await self._latest_close(stock_id, as_of) if spec.needs_close else None
            evidence, history = build_evidence(source, pit, close)
            # 메모리를 채점 **입력**으로: 이 소스의 직전 점수들을 PIT(analysis_date < as_of)로
            # 회상해 주입한다. 프롬프트 규범("증거가 실질적으로 안 변했으면 점수도 크게 변하면
            # 안 된다 · |Δ| > 0.3 이면 score_change_reason 필수")이 이 값으로 비로소 발화한다
            # — 점수 표류(삼성 7일 0.00→−0.60→0.00 실측)의 직접 대응책.
            prev_scores = await self._prev_scores(stock_id, spec.run_key, as_of)
            if prev_scores:
                history = {**history, "own_scores_recent": prev_scores}
            pit_by_ticker[ticker] = pit
            close_by_ticker[ticker] = close
            cohort.append(
                StockContext(
                    ticker=ticker,
                    name=name,
                    evidence=evidence,
                    self_history=history,
                    attention=build_attention(pit, as_of) if source == "DATALAB" else None,
                    prev_score=prev_scores[0]["score"] if prev_scores else None,
                )
            )

        if not cohort:
            return {"source": source, "status": "no_data", "no_data": no_data}

        results: list[SourceResult]
        llm_model: str | None = None
        client: Any | None = None
        try:
            client = self._build_client()
            scored = await score_cohort(client, source=source, asof=str(as_of), cohort=cohort)
            llm_model = client.model
            results = [
                to_source_result(s, source=source, stock_code=s.ticker, llm_model=llm_model)
                for s in scored
            ]
            mode = "llm"
        except Exception as exc:  # noqa: BLE001 — LlmScorerError/VertexError/transport 전부 폴백
            logger.warning("SCORE_COHORT %s LLM 실패 (%s) — 폴백=%s", source, exc, self._settings.llm_scoring_fallback)
            if self._settings.llm_scoring_fallback != "rules":
                # 아무것도 쓰지 않는다 — fan-in last-known 재사용이 어제 점수를 승계.
                return {
                    "source": source,
                    "status": "llm_failed_no_write",
                    "error": str(exc)[:300],
                    "cohort_size": len(cohort),
                }
            results = await self._rules_fallback(spec, cohort, pit_by_ticker, close_by_ticker, as_of, str(exc))
            mode = "rules_fallback"

        # 데이터 품질 검증 그래프 (opt-in) — 정규화·분석 적절성을 감사해 needs_review/
        # risk_flags 를 승격한다. 점수는 절대 바꾸지 않는다. 실패해도 발행을 못 막는다.
        validation_by_ticker: dict[str, Any] = {}
        if self._settings.llm_validation_enabled and results:
            try:
                validation_by_ticker = await self._validate(
                    source, as_of, pit_by_ticker, results,
                    client=client if mode == "llm" else None,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("SCORE_COHORT %s 데이터 품질 검증 실패(무시): %s", source, exc)

        stock_id_of = {t: sid for sid, t, _n in stocks}
        persistence = AlternativeSignalPersistence(
            self._connection,
            registrations=[_CohortRegistration(spec)],  # type: ignore[list-item] — duck-typed(.source/.debate_method)
            runtime_config=self._runtime,
        )
        published: list[dict[str, Any]] = []
        for result in results:
            stock_id = stock_id_of.get(result.stock_code)
            if stock_id is None:
                continue
            verdict = validation_by_ticker.get(result.stock_code)
            if verdict is not None and not verdict.ok:
                # 검증은 점수를 바꾸지 않는다 — 검토 플래그와 품질 사유만 승격.
                from dataclasses import replace

                result = replace(
                    result,
                    needs_review=True,
                    risk_flags=[*result.risk_flags, "data_quality"],
                )
            try:
                signal = build_source_signal(result, self._aggregator_config)
                ids = await persistence.save(
                    stock_id=stock_id,
                    signal=signal,
                    analysis_date=as_of,
                    publish_final_signal=True,
                    run_key=spec.run_key,
                )
                aggregate_task_id = await enqueue_aggregate(
                    self._queue_repository,
                    stock_id=stock_id,
                    aggregate_ctx={
                        "stock_code": result.stock_code,
                        "signal_date": as_of.isoformat(),
                        "run_key": "AGGREGATED",
                    },
                    priority="batch",
                )
                # 검증 결과를 관측 가능한 sink(validation_logs)에 남긴다 — 통과도 기록해
                # "검증이 돌았는데 깨끗했다"와 "검증이 안 돌았다"를 구분할 수 있게.
                if verdict is not None:
                    for agent_result_id in ids.get("agent_result_ids") or []:
                        await self._record_validation(agent_result_id, verdict)
            except Exception as exc:  # noqa: BLE001 — 한 종목 발행 실패 격리
                logger.warning(
                    "SCORE_COHORT %s: %s 발행 실패: %s", source, result.stock_code, exc
                )
                continue
            published.append(
                {
                    "ticker": result.stock_code,
                    "score": result.score,
                    "direction": result.direction,
                    "data_status": result.data_status,
                    "analysis_result_id": ids.get("analysis_result_id"),
                    "aggregate_task_id": aggregate_task_id,
                }
            )

        return {
            "source": source,
            "status": "success",
            "mode": mode,
            "llm_model": llm_model,
            "cohort_size": len(cohort),
            "published_count": len(published),
            "no_data": no_data,
            "published": published,
        }

    def _build_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from app.clients.json_llm import build_json_client

        return build_json_client(
            self._settings.llm_scoring_provider, self._settings.llm_scoring_model
        )

    async def _validate(
        self,
        source: str,
        as_of: date,
        pit_by_ticker: dict[str, list[dict]],
        results: list[SourceResult],
        *,
        client: Any | None,
    ) -> dict[str, Any]:
        """데이터 품질 검증 그래프 실행 → ticker→verdict. langgraph 는 지연 import
        (플래그 off 경로는 langgraph 미의존 — registry 의 cause 에이전트 관례)."""
        from app.agents.validation import ValidationGraphAgent

        scored = {
            r.stock_code: {
                "score": r.score,
                "direction": r.direction,
                "no_signal": r.data_status == "no_signal",
                "evidence": [item.summary for item in r.evidence_items],
            }
            for r in results
        }
        agent = ValidationGraphAgent(client=client)
        verdicts = await agent.validate(
            source=source, asof=as_of, pit_by_ticker=pit_by_ticker, scored=scored
        )
        return {v.ticker: v for v in verdicts}

    async def _record_validation(self, agent_result_id: int, verdict: Any) -> None:
        """validation_logs 기록 — 실패해도 발행을 못 막는다(best-effort)."""
        try:
            from signal_alpha_data_access.repositories import NormalizationRepository

            await NormalizationRepository(self._connection).record_validation_log(
                target_type="agent_result",
                target_id_int=int(agent_result_id),
                validation_type="data_quality",
                passed=bool(verdict.ok),
                message="; ".join(verdict.issues)[:500] or "clean",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("validation_logs 기록 실패(무시): %s", exc)

    async def _rules_fallback(
        self,
        spec: CohortSourceSpec,
        cohort: list[StockContext],
        pit_by_ticker: dict[str, list[dict]],
        close_by_ticker: dict[str, float | None],
        as_of: date,
        llm_error: str,
    ) -> list[SourceResult]:
        """결정론 채점기(reference_scorer)로 폴백 — 발행 공백 방지, 관측 가능하게.

        reference_scorer 는 수식 제거(D) 이후 결정론 채점 본체를 보존하는 파일이라
        (계측기 seam), 이 폴백은 D 이후에도 코드 이동 없이 유효하다."""
        from app.backtest.reference_scorer import SOURCES, score_source

        row = next((s for s in SOURCES if s[0] == spec.source), None)
        if row is None:
            return []
        _src, kind, _loader_key, _date_key, ind_fn, eval_fn, cfg_cls = row
        cfg = cfg_cls.from_env() if cfg_cls else None

        out: list[SourceResult] = []
        for stock in cohort:
            pit = pit_by_ticker.get(stock.ticker) or []
            try:
                score = float(
                    await score_source(
                        kind, pit, as_of, cfg, None, ind_fn, eval_fn,
                        current_close=close_by_ticker.get(stock.ticker),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("rules_fallback %s %s 실패: %s", spec.source, stock.ticker, exc)
                continue
            fallback = StockScore(
                ticker=stock.ticker,
                score=round(score, 3),
                confidence=0.0,
                no_signal=False,
                evidence=[f"LLM 호출 실패로 결정론 채점 폴백 (점수 {score:+.3f})"],
            )
            result = to_source_result(
                fallback, source=spec.source, stock_code=stock.ticker, llm_model=None
            )
            # provenance 를 폴백으로 바로잡는다(frozen dataclass → replace).
            from dataclasses import replace

            out.append(
                replace(
                    result,
                    analysis_source="rules_fallback",
                    prompt_ver=None,
                    llm_confidence=None,
                    llm_error=llm_error[:300],
                    summary=f"{spec.source} 결정론 폴백 채점 {score:+.3f} (LLM 실패)",
                )
            )
        return out

    async def _resolve_stocks(self, tickers: list[str]) -> list[tuple[int, str, str]]:
        rows = await self._connection.fetch(
            "SELECT id, ticker, name FROM stocks WHERE ticker = ANY($1::text[]) ORDER BY ticker",
            tickers,
        )
        return [(int(r["id"]), str(r["ticker"]), str(r["name"] or r["ticker"])) for r in rows]

    async def _prev_scores(
        self, stock_id: int, run_key: str, as_of: date, limit: int = 5
    ) -> list[dict[str, Any]]:
        """이 소스의 직전 채점들(부호 점수·최신순). ⚠️ PIT: ``analysis_date < as_of`` 만 —
        같은 날 재실행분을 넣으면 자기 출력을 되먹이는 루프가 된다."""
        rows = await self._connection.fetch(
            """
            SELECT ar.analysis_date, ag.method_detail->>'score' AS score
            FROM agent_results ag
            JOIN analysis_results ar ON ar.id = ag.result_id
            WHERE ar.stock_id = $1 AND ar.run_key = $2 AND ar.analysis_date < $3
              AND ag.method_detail->>'score' IS NOT NULL
            ORDER BY ar.analysis_date DESC
            LIMIT $4
            """,
            stock_id,
            run_key,
            as_of,
            limit,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                out.append({"date": str(r["analysis_date"]), "score": float(r["score"])})
            except (TypeError, ValueError):
                continue
        return out

    async def _latest_close(self, stock_id: int, as_of: date) -> float | None:
        value = await self._connection.fetchval(
            "SELECT close FROM ohlcv_data WHERE stock_id = $1 AND trade_date <= $2 "
            "ORDER BY trade_date DESC LIMIT 1",
            stock_id,
            as_of,
        )
        return float(value) if value is not None else None


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value).strip()[:10]).date()
