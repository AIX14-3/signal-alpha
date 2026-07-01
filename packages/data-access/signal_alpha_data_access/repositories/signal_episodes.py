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
                outcome = COALESCE(EXCLUDED.outcome, signal_episodes.outcome),
                created_at = NOW()
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
