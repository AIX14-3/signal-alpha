from __future__ import annotations

import json
from typing import Any


DEFAULT_DISCLAIMER = (
    "본 서비스가 제공하는 신호는 AI 에이전트의 데이터 분석 결과이며 "
    "투자 권유가 아닙니다. 투자 판단과 책임은 사용자 본인에게 있습니다."
)

# 종목의 "현재 발행 대표(AGGREGATED) 신호" 1건만 선택한다($1 = stock_id).
# is_current 는 (stock_id, signal_date, run_key) 단위로만 유일해 종목당 여러 행(소스별 run_key·
# 과거 signal_date)이 동시에 is_current=TRUE 일 수 있다. 오버레이/서술 UPDATE 가 이 모두를
# 덮어써 무관한 소스·과거일자 행을 오염시키던 버그를 막기 위해, run_key='AGGREGATED' + 최신
# signal_date 단일 행으로 좁힌다(없으면 0행 → no-op, 기존 "체인 순서상 없으면 다음 사이클" 보존).
_CURRENT_AGG_SUBQUERY = """(
    SELECT id FROM final_signals
    WHERE stock_id = $1 AND is_current = TRUE AND run_key = 'AGGREGATED'
    ORDER BY signal_date DESC, id DESC
    LIMIT 1
)"""


class AnalysisRepository:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def create_analysis_request(
        self,
        *,
        stock_id: int,
        user_id: int | None = None,
        analysis_mode: str = "full",
        ip_address: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO analysis_requests (
                user_id,
                stock_id,
                analysis_mode,
                ip_address
            )
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            user_id,
            stock_id,
            analysis_mode,
            ip_address,
        )

    async def complete_analysis_request(
        self,
        *,
        request_id: int,
        status: str,
        error_message: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            UPDATE analysis_requests
            SET
                status = $2,
                completed_at = NOW(),
                error_message = $3
            WHERE id = $1
            RETURNING *
            """,
            request_id,
            status,
            error_message,
        )

    async def upsert_analysis_result(
        self,
        *,
        stock_id: int,
        analysis_date: Any,
        run_key: str,
        source_signal_event_ids: list[int],
        base_score: Any,
        analysis_mode: str = "full",
        version: str = "1.0",
        request_id: int | None = None,
        pre_xgb_score: Any | None = None,
        xgb_adj: Any | None = None,
        warning: str | None = None,
        disclaimer: str | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO analysis_results (
                request_id,
                stock_id,
                analysis_date,
                run_key,
                source_signal_event_ids,
                base_score,
                pre_xgb_score,
                xgb_adj,
                analysis_mode,
                warning,
                disclaimer,
                version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (stock_id, analysis_date, analysis_mode, run_key, version)
            DO UPDATE SET
                request_id = EXCLUDED.request_id,
                source_signal_event_ids = EXCLUDED.source_signal_event_ids,
                base_score = EXCLUDED.base_score,
                pre_xgb_score = EXCLUDED.pre_xgb_score,
                xgb_adj = EXCLUDED.xgb_adj,
                warning = EXCLUDED.warning,
                disclaimer = EXCLUDED.disclaimer
            RETURNING *
            """,
            request_id,
            stock_id,
            analysis_date,
            run_key,
            source_signal_event_ids,
            base_score,
            pre_xgb_score,
            xgb_adj,
            analysis_mode,
            warning,
            disclaimer or DEFAULT_DISCLAIMER,
            version,
        )

    async def upsert_agent_result(
        self,
        *,
        result_id: int,
        stock_id: int,
        debate_method: str,
        method_score: Any,
        method_signal: str,
        method_detail: Any,
        source_signal_event_ids: list[int] | None = None,
        reliability_score: Any | None = None,
        evidence_quality: Any | None = None,
        llm_model: str | None = None,
        prompt_ver: str = "1.0",
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO agent_results (
                result_id,
                stock_id,
                debate_method,
                source_signal_event_ids,
                method_score,
                method_signal,
                method_detail,
                reliability_score,
                evidence_quality,
                llm_model,
                prompt_ver
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (result_id, debate_method)
            DO UPDATE SET
                source_signal_event_ids = EXCLUDED.source_signal_event_ids,
                method_score = EXCLUDED.method_score,
                method_signal = EXCLUDED.method_signal,
                method_detail = EXCLUDED.method_detail,
                reliability_score = EXCLUDED.reliability_score,
                evidence_quality = EXCLUDED.evidence_quality,
                llm_model = EXCLUDED.llm_model,
                prompt_ver = EXCLUDED.prompt_ver
            RETURNING *
            """,
            result_id,
            stock_id,
            debate_method,
            source_signal_event_ids,
            method_score,
            method_signal,
            _jsonb(method_detail),
            reliability_score,
            evidence_quality,
            llm_model,
            prompt_ver,
        )

    async def list_dart_analysis_results(
        self,
        *,
        stock_code: str,
        analysis_date: Any | None = None,
        limit: int = 20,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                ar.id,
                ar.stock_id,
                stocks.ticker AS stock_code,
                stocks.name AS stock_name,
                ar.analysis_date,
                ar.run_key,
                ar.analysis_mode,
                ar.version,
                ar.source_signal_event_ids,
                ar.base_score,
                ar.pre_xgb_score,
                ar.xgb_adj,
                ar.warning,
                ar.disclaimer,
                ar.created_at,
                COALESCE(agent_results.items, '[]'::JSONB) AS agent_results,
                COALESCE(signal_events.items, '[]'::JSONB) AS signal_events
            FROM analysis_results ar
            INNER JOIN stocks
                ON stocks.id = ar.stock_id
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', agent_results.id,
                        'debate_method', agent_results.debate_method,
                        'method_score', agent_results.method_score,
                        'method_signal', agent_results.method_signal,
                        'method_detail', agent_results.method_detail,
                        'source_signal_event_ids', agent_results.source_signal_event_ids,
                        'reliability_score', agent_results.reliability_score,
                        'evidence_quality', agent_results.evidence_quality,
                        'llm_model', agent_results.llm_model,
                        'prompt_ver', agent_results.prompt_ver,
                        'created_at', agent_results.created_at
                    )
                    ORDER BY agent_results.debate_method
                ) AS items
                FROM agent_results
                WHERE agent_results.result_id = ar.id
            ) agent_results ON TRUE
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', signal_events.id,
                        'source_document_id', signal_events.source_document_id,
                        'source_type', signal_events.source_type,
                        'event_type', signal_events.event_type,
                        'event_date', signal_events.event_date,
                        'signal_direction', signal_events.signal_direction,
                        'impact_level', signal_events.impact_level,
                        'title', signal_events.title,
                        'summary', signal_events.summary,
                        'evidence_url', signal_events.evidence_url,
                        'needs_review', signal_events.needs_review
                    )
                    ORDER BY signal_events.event_date DESC, signal_events.id DESC
                ) AS items
                FROM signal_events
                WHERE signal_events.id = ANY(ar.source_signal_event_ids)
                  AND signal_events.source_type = 'DART'
            ) signal_events ON TRUE
            WHERE stocks.ticker = $1
              AND ($2::DATE IS NULL OR ar.analysis_date = $2::DATE)
              AND ar.run_key LIKE 'DART%'
            ORDER BY ar.analysis_date DESC, ar.created_at DESC, ar.id DESC
            LIMIT $3
            """,
            stock_code,
            analysis_date,
            limit,
        )

    async def list_latest_source_results_for_stock(
        self,
        *,
        stock_id: int,
        analysis_date: Any,
        long_window_days: int = 30,
        short_window_days: int = 7,
    ) -> list[Any]:
        """Fan-in: the latest per-source agent_result for one stock, within a window.

        Returns the same row shape as ``list_agent_results_for_aggregation`` so the
        aggregation handler can normalize either source identically. Unlike that
        method (which takes an explicit id list a single producer passes in), this
        gathers EVERY independent source signal that exists for the stock —
        DART/PRICE/REPORT/HIRING/PATENT/DATALAB each under their own run_key — and
        keeps the newest analysis_result per run_key (``DISTINCT ON``). The
        cross-source ``AGGREGATED`` / ``ML`` / ``META`` run_keys are excluded so the
        aggregate never folds a prior aggregate back into itself.

        **Last-known reuse:** instead of an exact-date match, each source falls back
        to its most recent result on or before ``analysis_date`` within a per-source
        validity window — slow-moving sources (DART/PATENT/REPORT) reuse up to
        ``long_window_days``, fast-moving ones (HIRING/DATALAB/PRICE) up to
        ``short_window_days``. ``data_age_days`` exposes how stale each reused row is
        (0 = same-day) so the aggregate/synthesis can surface "최종 업데이트 N일 전".
        Rows older than their window are dropped (that source shows ``missing``).
        """
        return await self._connection.fetch(
            """
            SELECT DISTINCT ON (analysis_results.run_key)
                analysis_results.id AS analysis_result_id,
                analysis_results.stock_id,
                analysis_results.analysis_date,
                ($2::DATE - analysis_results.analysis_date) AS data_age_days,
                analysis_results.run_key AS analysis_run_key,
                analysis_results.analysis_mode,
                analysis_results.version AS analysis_version,
                analysis_results.source_signal_event_ids AS analysis_source_signal_event_ids,
                agent_results.id AS agent_result_id,
                agent_results.debate_method,
                agent_results.source_signal_event_ids AS agent_source_signal_event_ids,
                agent_results.method_score,
                agent_results.method_signal,
                agent_results.method_detail,
                agent_results.reliability_score,
                agent_results.evidence_quality,
                agent_results.llm_model,
                agent_results.prompt_ver
            FROM agent_results
            INNER JOIN analysis_results
                ON analysis_results.id = agent_results.result_id
            WHERE analysis_results.stock_id = $1
              AND analysis_results.analysis_date <= $2::DATE
              AND analysis_results.analysis_date >= $2::DATE - (
                    CASE
                        WHEN analysis_results.run_key LIKE 'DART%'
                          OR analysis_results.run_key LIKE 'PATENT%'
                          OR analysis_results.run_key LIKE 'REPORT%' THEN $3::INT
                        ELSE $4::INT
                    END
                  )
              AND (
                    analysis_results.run_key LIKE 'DART%'
                 OR analysis_results.run_key LIKE 'PRICE%'
                 OR analysis_results.run_key LIKE 'REPORT%'
                 OR analysis_results.run_key LIKE 'HIRING%'
                 OR analysis_results.run_key LIKE 'PATENT%'
                 OR analysis_results.run_key LIKE 'DATALAB%'
              )
            ORDER BY
                analysis_results.run_key,
                analysis_results.analysis_date DESC,
                analysis_results.created_at DESC,
                analysis_results.id DESC,
                agent_results.id DESC
            """,
            stock_id,
            analysis_date,
            long_window_days,
            short_window_days,
        )

    async def list_agent_results_for_aggregation(self, analysis_result_ids: list[int]) -> list[Any]:
        if not analysis_result_ids:
            return []
        return await self._connection.fetch(
            """
            SELECT
                analysis_results.id AS analysis_result_id,
                analysis_results.stock_id,
                analysis_results.analysis_date,
                analysis_results.run_key AS analysis_run_key,
                analysis_results.analysis_mode,
                analysis_results.version AS analysis_version,
                analysis_results.source_signal_event_ids AS analysis_source_signal_event_ids,
                agent_results.id AS agent_result_id,
                agent_results.debate_method,
                agent_results.source_signal_event_ids AS agent_source_signal_event_ids,
                agent_results.method_score,
                agent_results.method_signal,
                agent_results.method_detail,
                agent_results.reliability_score,
                agent_results.evidence_quality,
                agent_results.llm_model,
                agent_results.prompt_ver
            FROM agent_results
            INNER JOIN analysis_results
                ON analysis_results.id = agent_results.result_id
            WHERE agent_results.result_id = ANY($1::BIGINT[])
            ORDER BY analysis_results.analysis_date DESC, agent_results.id DESC
            """,
            analysis_result_ids,
        )

    async def list_final_signals_by_stock(
        self,
        *,
        stock_code: str,
        signal_date: Any | None = None,
        bgn_de: Any | None = None,
        end_de: Any | None = None,
        run_key: str = "AGGREGATED",
        limit: int = 20,
    ) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT
                final_signals.id,
                final_signals.stock_id,
                stocks.ticker AS stock_code,
                stocks.name AS stock_name,
                final_signals.analysis_result_id,
                final_signals.signal_date,
                final_signals.run_key,
                final_signals.version,
                final_signals.final_score,
                final_signals.confidence,
                final_signals.signal,
                final_signals.source_agreement,
                final_signals.warning_level,
                final_signals.score_breakdown,
                final_signals.summary,
                final_signals.bull_point,
                final_signals.bear_point,
                final_signals.disclaimer,
                final_signals.needs_review,
                final_signals.min_plan_required,
                final_signals.is_published,
                final_signals.published_at,
                final_signals.consensus_score,
                final_signals.positive_evidence,
                final_signals.caution_evidence,
                final_signals.created_at
            FROM final_signals
            INNER JOIN stocks
                ON stocks.id = final_signals.stock_id
            WHERE stocks.ticker = $1
              AND ($2::DATE IS NULL OR final_signals.signal_date = $2::DATE)
              AND ($3::DATE IS NULL OR final_signals.signal_date >= $3::DATE)
              AND ($4::DATE IS NULL OR final_signals.signal_date <= $4::DATE)
              AND final_signals.run_key = $5
            ORDER BY final_signals.signal_date DESC,
                     final_signals.created_at DESC,
                     final_signals.id DESC
            LIMIT $6
            """,
            stock_code,
            signal_date,
            bgn_de,
            end_de,
            run_key,
            limit,
        )

    async def upsert_final_signal(
        self,
        *,
        stock_id: int,
        analysis_result_id: int,
        signal_date: Any,
        run_key: str,
        version: str,
        final_score: Any,
        confidence: Any,
        signal: str,
        source_agreement: str,
        score_breakdown: Any,
        summary: str,
        warning_level: str = "NORMAL",
        bull_point: str | None = None,
        bear_point: str | None = None,
        disclaimer: str | None = None,
        needs_review: bool = False,
        min_plan_required: str = "free",
        is_published: bool = False,
        published_at: Any | None = None,
        consensus_score: Any = None,
        positive_evidence: Any = None,
        caution_evidence: Any = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO final_signals (
                stock_id,
                analysis_result_id,
                signal_date,
                run_key,
                version,
                final_score,
                confidence,
                signal,
                source_agreement,
                warning_level,
                score_breakdown,
                summary,
                bull_point,
                bear_point,
                disclaimer,
                needs_review,
                min_plan_required,
                is_published,
                published_at,
                consensus_score,
                positive_evidence,
                caution_evidence
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15, $16, $17, $18, $19,
                $20, $21, $22
            )
            ON CONFLICT (stock_id, signal_date, run_key, version)
            DO UPDATE SET
                analysis_result_id = EXCLUDED.analysis_result_id,
                final_score = EXCLUDED.final_score,
                confidence = EXCLUDED.confidence,
                signal = EXCLUDED.signal,
                source_agreement = EXCLUDED.source_agreement,
                warning_level = EXCLUDED.warning_level,
                score_breakdown = EXCLUDED.score_breakdown,
                summary = EXCLUDED.summary,
                bull_point = EXCLUDED.bull_point,
                bear_point = EXCLUDED.bear_point,
                disclaimer = EXCLUDED.disclaimer,
                needs_review = EXCLUDED.needs_review,
                min_plan_required = EXCLUDED.min_plan_required,
                is_published = EXCLUDED.is_published,
                published_at = EXCLUDED.published_at,
                consensus_score = EXCLUDED.consensus_score,
                positive_evidence = EXCLUDED.positive_evidence,
                caution_evidence = EXCLUDED.caution_evidence
            RETURNING *
            """,
            stock_id,
            analysis_result_id,
            signal_date,
            run_key,
            version,
            final_score,
            confidence,
            signal,
            source_agreement,
            warning_level,
            _jsonb(score_breakdown),
            summary,
            bull_point,
            bear_point,
            disclaimer or DEFAULT_DISCLAIMER,
            needs_review,
            min_plan_required,
            is_published,
            published_at,
            consensus_score,
            _jsonb(positive_evidence),
            _jsonb(caution_evidence),
        )


    async def get_final_signal_by_id(self, *, final_signal_id: int) -> Any:
        """발행 여부와 무관하게 final_signal 1건 조회(끝단 종합은 vetoed/미발행도 설명)."""
        return await self._connection.fetchrow(
            "SELECT * FROM final_signals WHERE id = $1",
            final_signal_id,
        )

    async def update_final_signal_narrative(
        self,
        *,
        final_signal_id: int,
        summary: str,
        bull_point: str | None = None,
        bear_point: str | None = None,
    ) -> Any:
        """끝단 LLM 종합 결과(설명)만 갱신 — 점수/방향/발행 플래그는 건드리지 않는다."""
        return await self._connection.fetchrow(
            """
            UPDATE final_signals
            SET
                summary = $2,
                bull_point = $3,
                bear_point = $4
            WHERE id = $1
            RETURNING *
            """,
            final_signal_id,
            summary,
            bull_point,
            bear_point,
        )

    async def update_source_narrative(
        self,
        *,
        stock_id: int,
        source: str,
        summary: str,
        narrative_points: Any = None,
        mark_narrated: bool = True,
    ) -> Any:
        """소스별 LLM 서술(summary/narrative_points)을 ``score_breakdown.{SOURCE}`` 에 병합한다.

        C안 정합: 방향/점수/score_100 등 **수치 필드는 불변**(서술만). ``jsonb_set`` 으로 해당 소스
        객체에 ``summary``·``narrative_points``·``narrated`` 플래그만 덮어쓴다(소스 객체가 없으면 생성).
        ``mark_narrated`` 시 ``data_status`` 가 'missing' 이면 'ok' 로 올려, 서술이 있는데도 카드가
        "데이터 수집 전"으로 보이는 표시 불일치를 막는다(표시 상태만 보정, 점수/방향 불변). 현재 발행
        신호(is_current)에 적용 — 체인 순서상 아직 없으면 no-op(다음 사이클).
        """
        merge_obj = {
            "summary": summary,
            "narrative_points": narrative_points if narrative_points is not None else [],
            "narrated": True,
        }
        if mark_narrated:
            # data_status 가 'missing' 일 때만 'ok' 로 보정(이미 ok/no_signal/partial 이면 보존).
            return await self._connection.fetchrow(
                """
                UPDATE final_signals
                SET score_breakdown = jsonb_set(
                    COALESCE(score_breakdown, '{}'::jsonb),
                    ARRAY[$2],
                    COALESCE(score_breakdown -> $2, '{}'::jsonb)
                        || $3::jsonb
                        || CASE WHEN COALESCE(score_breakdown -> $2 ->> 'data_status', 'missing') = 'missing'
                                THEN jsonb_build_object('data_status', 'ok') ELSE '{}'::jsonb END,
                    TRUE
                )
                WHERE id = """
                + _CURRENT_AGG_SUBQUERY
                + """
                RETURNING id
                """,
                stock_id,
                source,
                _jsonb(merge_obj),
            )
        return await self._connection.fetchrow(
            """
            UPDATE final_signals
            SET score_breakdown = jsonb_set(
                COALESCE(score_breakdown, '{}'::jsonb),
                ARRAY[$2],
                COALESCE(score_breakdown -> $2, '{}'::jsonb) || $3::jsonb,
                TRUE
            )
            WHERE id = """
            + _CURRENT_AGG_SUBQUERY
            + """
            RETURNING id
            """,
            stock_id,
            source,
            _jsonb(merge_obj),
        )

    async def attach_evidence_events(
        self,
        *,
        stock_id: int,
        event_ids: list[int],
    ) -> Any:
        """현재 발행 신호(is_current)의 분석결과에 근거 signal_event id 를 합집합 추가한다.

        리포트 소스 상세의 근거 목록(api.signal_detail.signal_events)은
        ``analysis_results.source_signal_event_ids`` 로 해석된다. 서술(narrate)이 사용한 소스
        이벤트를 그 배열에 멱등 합집합으로 더해, 집계에 소스 분석이 빠져 근거가 비던 화면을 채운다.
        점수/방향 등 다른 필드는 불변.
        """
        if not event_ids:
            return None
        return await self._connection.execute(
            """
            UPDATE analysis_results ar
            SET source_signal_event_ids = (
                SELECT array_agg(DISTINCT x)
                FROM unnest(
                    COALESCE(ar.source_signal_event_ids, '{}'::bigint[]) || $2::bigint[]
                ) AS x
            )
            FROM final_signals fs
            WHERE fs.analysis_result_id = ar.id
              AND fs.id = """
            + _CURRENT_AGG_SUBQUERY
            + """
            """,
            stock_id,
            [int(e) for e in event_ids],
        )

    async def update_final_signal_return_channel(
        self,
        *,
        stock_id: int,
        ml_final_score: float | None,
        ml_direction: str | None,
        ml_confidence: float | None,
        source_predictions: Any = None,
    ) -> Any:
        """메타러너 return 채널(#525 WS-C)을 종목의 현재 신호에 오버레이.

        결정론 집계 점수(final_score/signal/confidence)는 건드리지 않고 ml_*/source_predictions
        컬럼만 갱신(D4). ``source_predictions`` 는 소스별 6 + 통합 1 = 7개 예측률 JSONB(C안 P3,
        ``{run_key: {final_score, direction, confidence, model_count}}``). 현재 발행 신호
        (is_current)가 아직 없으면(체인 순서) no-op — 다음 사이클에 채워진다.
        """
        return await self._connection.fetchrow(
            """
            UPDATE final_signals
            SET
                ml_final_score = $2,
                ml_direction = $3,
                ml_confidence = $4,
                source_predictions = $5::jsonb
            WHERE id = """
            + _CURRENT_AGG_SUBQUERY
            + """
            RETURNING *
            """,
            stock_id,
            ml_final_score,
            ml_direction,
            ml_confidence,
            _jsonb(source_predictions),
        )


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
