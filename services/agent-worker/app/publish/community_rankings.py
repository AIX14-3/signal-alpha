"""커뮤니티 게시글 인기 랭킹 배치 — backend DB 단독(집계 소스·랭킹 스냅샷 공존).

community_posts/comments/reactions/post_views 를 집계해 가중합 점수를 내고
community_post_rankings 에 (post_id, window_kind) 로 멱등 upsert 한다. 두 윈도우:
'weekly'(롤링 7일 — 최근 7일 활동만 집계) / 'all'(전체 기간). run_journal_outcomes
와 같은 워커→백엔드 계약(순수 SQL·연결 주입형이라 fake 로 단위테스트 가능).

가중치·주기는 튜닝값(spec §7): 초기 like=3 / comment=5 / view=1, 시간당 배치. 숨김·삭제
게시글은 집계 대상에서 빠지고, 그 게시글의 이전 스냅샷 행은 prune 으로 제거한다(피드 정합).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Weights:
    """가중합 계수(튜닝값). score = like*w_like + comment*w_comment + view*w_view."""

    like: int = 3
    comment: int = 5
    view: int = 1


DEFAULT_WEIGHTS = Weights()

# (window_kind, 롤링 일수). window_kind 는 community_post_rankings CHECK 와 일치해야 한다.
# days=None → 전체 기간(cutoff 없음).
WINDOWS: tuple[tuple[str, int | None], ...] = (("weekly", 7), ("all", None))

# 게시글별 활동 집계. cutoff($1) 이 NULL 이면 전체 기간, 아니면 그 시각 이후만.
# 숨김/삭제 게시글은 WHERE 로 제외 → 랭킹 대상에서 자동 배제된다. 댓글도 visible 만 센다.
_AGG_SQL = """
SELECT
    p.id AS post_id,
    COALESCE(l.cnt, 0) AS likes,
    COALESCE(c.cnt, 0) AS comments,
    COALESCE(v.cnt, 0) AS views
FROM community_posts p
LEFT JOIN (
    SELECT target_id, COUNT(*) AS cnt
    FROM community_reactions
    WHERE target_type = 'post' AND type = 'like'
      AND ($1::timestamptz IS NULL OR created_at >= $1)
    GROUP BY target_id
) l ON l.target_id = p.id
LEFT JOIN (
    SELECT post_id, COUNT(*) AS cnt
    FROM community_comments
    WHERE status = 'visible' AND deleted_at IS NULL
      AND ($1::timestamptz IS NULL OR created_at >= $1)
    GROUP BY post_id
) c ON c.post_id = p.id
LEFT JOIN (
    SELECT post_id, COUNT(*) AS cnt
    FROM community_post_views
    WHERE ($1::timestamptz IS NULL OR created_at >= $1)
    GROUP BY post_id
) v ON v.post_id = p.id
WHERE p.deleted_at IS NULL AND p.status = 'visible'
"""

_UPSERT_SQL = """
INSERT INTO community_post_rankings (post_id, window_kind, score, likes, comments, views, computed_at)
VALUES ($1, $2, $3, $4, $5, $6, NOW())
ON CONFLICT (post_id, window_kind) DO UPDATE SET
    score = EXCLUDED.score,
    likes = EXCLUDED.likes,
    comments = EXCLUDED.comments,
    views = EXCLUDED.views,
    computed_at = NOW()
"""

# 이번 실행에서 더 이상 노출되지 않는(숨김/삭제된) 게시글의 스냅샷 행을 지운다.
# active_ids 가 빈 배열이면 `<> ALL('{}')` 은 항상 참 → 해당 윈도우 전체 정리.
_PRUNE_SQL = """
DELETE FROM community_post_rankings
WHERE window_kind = $1
  AND post_id <> ALL($2::bigint[])
"""


@dataclass
class RankingStats:
    windows: int = 0  # 집계 완료한 윈도우 수
    upserts: int = 0  # upsert 된 (post, window) 행 수
    pruned: int = 0  # prune 으로 제거된 스냅샷 행 수
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def compute_score(likes: int, comments: int, views: int, weights: Weights = DEFAULT_WEIGHTS) -> int:
    """활동 카운트 → 가중합 점수(정수)."""
    return likes * weights.like + comments * weights.comment + views * weights.view


async def record_community_rankings(
    conn: Any,
    *,
    now: datetime,
    weights: Weights = DEFAULT_WEIGHTS,
    windows: tuple[tuple[str, int | None], ...] = WINDOWS,
) -> RankingStats:
    """윈도우별로 게시글 활동을 집계해 랭킹 스냅샷을 멱등 upsert + 정리한다.

    weekly cutoff = now - N일. now 는 tz-aware(KST) 권장 — DB timestamptz 와 비교한다.
    """
    stats = RankingStats()
    for window_kind, days in windows:
        cutoff = None if days is None else now - timedelta(days=days)
        try:
            rows = await conn.fetch(_AGG_SQL, cutoff)
        except Exception as exc:  # noqa: BLE001 - 한 윈도우 실패가 다른 윈도우를 막지 않음
            stats.failed += 1
            stats.errors.append(f"window={window_kind} aggregate: {exc}")
            logger.warning("ranking aggregate failed for window=%s: %s", window_kind, exc)
            continue

        # 노출 중인 게시글 전체가 이번 윈도우의 "살아있는" 집합 — upsert 성공 여부와 무관하게
        # prune 대상에서 보호한다(일시적 upsert 실패로 유효 행을 지우지 않도록).
        active_ids = [int(row["post_id"]) for row in rows]
        for row in rows:
            post_id = int(row["post_id"])
            likes = int(row["likes"])
            comments = int(row["comments"])
            views = int(row["views"])
            score = Decimal(compute_score(likes, comments, views, weights))
            try:
                await conn.execute(_UPSERT_SQL, post_id, window_kind, score, likes, comments, views)
                stats.upserts += 1
            except Exception as exc:  # noqa: BLE001 - 게시글 1건 실패가 전체를 막지 않음
                stats.failed += 1
                stats.errors.append(f"window={window_kind} post={post_id}: {exc}")
                logger.warning(
                    "ranking upsert failed for window=%s post=%s: %s", window_kind, post_id, exc
                )

        try:
            result = await conn.execute(_PRUNE_SQL, window_kind, active_ids)
            stats.pruned += _rowcount(result)
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            stats.errors.append(f"window={window_kind} prune: {exc}")
            logger.warning("ranking prune failed for window=%s: %s", window_kind, exc)
        stats.windows += 1
    return stats


def _rowcount(command_tag: Any) -> int:
    """asyncpg execute 반환 태그('DELETE N')에서 영향 행 수를 파싱한다."""
    try:
        return int(str(command_tag).split()[-1])
    except (ValueError, IndexError):
        return 0
