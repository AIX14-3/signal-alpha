from __future__ import annotations

import json
from typing import Any


def _vector_literal(embedding: list[float]) -> str:
    """Render a float list as a pgvector text literal (``[0.1,0.2,...]``).

    asyncpg has no native codec for the ``vector`` type, so we pass the value as
    text and let Postgres cast it (``$n::vector``). ``repr``-free formatting keeps
    the literal compact and locale-independent.
    """
    return "[" + ",".join(format(float(v), ".8g") for v in embedding) + "]"


class SignalEpisodeRepository:
    """signal_episodes 적재/회상 — 시그널 발화 1건의 에피소드 메모리 원장(에이전트화 Stage 2).

    한 (stock_id, signal_date, run_key) = 1 에피소드를 자연키로 멱등 upsert 한다. 발화 소스/
    방향/점수 요약(``sources`` JSONB)과 상황 임베딩(``vector(768)``)을 보관하고, 성패
    (``outcome`` JSONB)는 사후에 채운다(NULL 시작). 오케스트레이터가 ``recall_similar`` 로
    현재 상황 벡터의 코사인 최근접 과거 에피소드를 회상해 judge/reflection 에 '참고'로만
    주입한다 — 숫자(headline)엔 절대 반영하지 않는다(불변식).
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_episode(
        self,
        *,
        stock_id: int,
        signal_date: Any,
        run_key: str,
        embedding: list[float],
        direction: str | None = None,
        score: float | None = None,
        sources: Any | None = None,
        outcome: Any | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO signal_episodes (
                stock_id, signal_date, run_key,
                direction, score, sources, embedding, outcome
            )
            VALUES ($1, $2, $3, $4, $5, $6::JSONB, $7::vector, $8::JSONB)
            ON CONFLICT (stock_id, signal_date, run_key)
            DO UPDATE SET
                direction = EXCLUDED.direction,
                score = EXCLUDED.score,
                sources = EXCLUDED.sources,
                embedding = EXCLUDED.embedding,
                outcome = COALESCE(EXCLUDED.outcome, signal_episodes.outcome)
            RETURNING id
            """,
            stock_id,
            signal_date,
            run_key,
            direction,
            score,
            _jsonb(sources),
            _vector_literal(embedding),
            _jsonb(outcome),
        )

    async def list_pending_outcomes(
        self,
        *,
        min_horizon_days: int,
        limit: int = 500,
    ) -> list[Any]:
        """사후 결과 기록 대기 에피소드(진행형 outcome 리코더용, Wave 3 후속②).

        가장 짧은 horizon(``min_horizon_days``)조차 아직 만기되지 않은 에피소드는 계산할 게
        없으므로 제외한다(``signal_date <= 오늘 - min_horizon``). ``outcome.complete='true'`` 로
        모든 horizon 이 확정된 에피소드도 제외한다 — 나머지(NULL 또는 일부만 채워진 것)만 돌려주고,
        리코더가 만기된 horizon 을 progressive 로 채운다. ``outcome`` 도 함께 실어 이미 채운 horizon
        을 skip 하게 한다. 오래된 것부터(ORDER BY signal_date ASC) 처리한다.
        """
        return await self._connection.fetch(
            """
            SELECT id, stock_id, signal_date, run_key, direction, score, outcome
            FROM signal_episodes
            WHERE signal_date <= (CURRENT_DATE - ($1::int))
              AND COALESCE(outcome->>'complete', '') <> 'true'
            ORDER BY signal_date ASC
            LIMIT $2
            """,
            int(min_horizon_days),
            int(limit),
        )

    async def merge_outcome(self, *, episode_id: int, patch: Any) -> Any:
        """outcome jsonb 에 새 horizon 결과를 top-level 병합(``||``)한다(진행형 기록).

        기존 키(이미 채운 horizon)는 보존하고 patch 의 새 키만 더한다 — 짧은 horizon 을 먼저
        채우고 60일은 나중에 만기되면 덧붙이는 progressive 기록을 재백필 없이 지원한다. 리코더가
        만기·미채움 horizon 만 patch 로 넘기므로 재실행/부분채움 모두 멱등하다(사후 기록·recall
        참고용일 뿐 headline 숫자엔 영향 없음 — 불변식).
        """
        return await self._connection.fetchrow(
            """
            UPDATE signal_episodes
            SET outcome = COALESCE(outcome, '{}'::JSONB) || $2::JSONB
            WHERE id = $1
            RETURNING id
            """,
            int(episode_id),
            _jsonb(patch),
        )

    async def recall_similar(
        self,
        *,
        embedding: list[float],
        exclude_stock_id: int | None = None,
        exclude_signal_date: Any | None = None,
        limit: int = 3,
    ) -> list[Any]:
        """현재 상황 벡터의 코사인 최근접 과거 에피소드 top-k(오래된 순 아님, 유사도 순).

        ``embedding <=> $1`` 는 pgvector 코사인 거리(작을수록 유사). 방금 발행 중인 바로 그
        (stock, date) 자기 자신은 제외해 자기회상을 막는다. 이력이 없으면 빈 리스트(콜드스타트)라
        오케스트레이터는 메모리 없이 그대로 진행한다.
        """
        return await self._connection.fetch(
            """
            SELECT
                stock_id,
                signal_date,
                run_key,
                direction,
                score,
                sources,
                outcome,
                (embedding <=> $1::vector) AS distance
            FROM signal_episodes
            WHERE NOT (
                $2::BIGINT IS NOT NULL AND stock_id = $2
                AND $3::DATE IS NOT NULL AND signal_date = $3::DATE
            )
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,
            _vector_literal(embedding),
            exclude_stock_id,
            exclude_signal_date,
            limit,
        )


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
