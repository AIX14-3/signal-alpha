"""커뮤니티 게시판 리포지토리 (backend DB).

저널 커뮤니티 게시판 — 유저가 저널(투자 판단 기록)을 공개 공유하는 소셜 레이어.
읽기 전체 공개 / 쓰기 로그인. 공개 응답은 화이트리스트 컬럼만 — 저널의 손익/가격·PII 는
노출하지 않는다(수익률%는 show_pnl opt-in 시에만). 저자·저널은 라이브 참조(같은 backend DB).

asyncpg 스타일: 키워드 전용 인자, $n 위치 파라미터, fetchrow/fetch/fetchval/execute,
반환은 raw Record(호출부에서 dict() 래핑).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# 공개 피드/상세가 노출하는 저널 화이트리스트 + 저자 핸들 + 집계 카운트.
# 가격/절대손익/PII 는 SELECT 목록에 넣지 않는다(NFR-1). 수익률%는 show_pnl 일 때만.
_POST_SELECT = """
    p.id, p.title, p.body, p.show_pnl, p.view_count, p.status,
    p.created_at, p.updated_at, p.journal_id, p.author_user_id,
    u.member_code AS author_member_code, u.nickname AS author_nickname,
    j.user_view AS journal_user_view, j.user_memo AS journal_user_memo,
    s.ticker AS stock_ticker, s.name AS stock_name,
    -- 손익(수익률%)은 확정 outcome(signal_journal_outcomes)의 가장 최근 horizon 값.
    -- show_pnl opt-in 일 때만 노출. 저널 본체엔 손익 컬럼이 없다(legacy drop, 20260702_1401).
    CASE WHEN p.show_pnl THEN (
        SELECT o.change_pct FROM signal_journal_outcomes o
         WHERE o.journal_id = p.journal_id
         ORDER BY o.outcome_trade_date DESC
         LIMIT 1
    ) ELSE NULL END AS pnl_pct,
    (SELECT count(*) FROM community_reactions r
       WHERE r.target_type = 'post' AND r.target_id = p.id AND r.type = 'like') AS like_count,
    (SELECT count(*) FROM community_comments c
       WHERE c.post_id = p.id AND c.deleted_at IS NULL AND c.status = 'visible') AS comment_count
"""

_POST_JOINS = """
    FROM community_posts p
    JOIN users u ON u.id = p.author_user_id
    LEFT JOIN signal_journals j ON j.id = p.journal_id
    LEFT JOIN api.stocks s ON s.id = j.stock_id
"""


class CommunityRepository:
    """커뮤니티 게시글·댓글·반응·조회·신고·랭킹 조회. backend 연결 위에서 동작."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    # ---- 게시글 -----------------------------------------------------------
    async def get_journal_author(self, *, journal_id: int) -> int | None:
        """저널 소유자 user_id(공유 권한 검증용). 없으면 None."""
        return await self._connection.fetchval(
            "SELECT user_id FROM signal_journals WHERE id = $1", journal_id
        )

    async def create_post(
        self,
        *,
        author_user_id: int,
        journal_id: int | None,
        title: str,
        body: str,
        show_pnl: bool,
    ) -> Any:
        post_id = await self._connection.fetchval(
            """
            INSERT INTO community_posts (author_user_id, journal_id, title, body, show_pnl)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            author_user_id,
            journal_id,
            title,
            body,
            show_pnl,
        )
        return await self.get_post(post_id=post_id)

    async def get_post(self, *, post_id: int) -> Any:
        """공개 상세 1건(삭제/숨김 제외). 화이트리스트 컬럼만."""
        return await self._connection.fetchrow(
            f"""
            SELECT {_POST_SELECT}
            {_POST_JOINS}
            WHERE p.id = $1 AND p.deleted_at IS NULL AND p.status = 'visible'
            """,
            post_id,
        )

    async def get_post_author(self, *, post_id: int) -> int | None:
        return await self._connection.fetchval(
            "SELECT author_user_id FROM community_posts WHERE id = $1 AND deleted_at IS NULL",
            post_id,
        )

    async def list_feed(self, *, cursor_id: int | None, limit: int) -> list[Any]:
        """최신 피드. keyset 커서(id DESC) — cursor_id 이전 것부터. 비로그인 열람 가능."""
        return await self._connection.fetch(
            f"""
            SELECT {_POST_SELECT}
            {_POST_JOINS}
            WHERE p.deleted_at IS NULL AND p.status = 'visible'
              AND ($1::bigint IS NULL OR p.id < $1)
            ORDER BY p.id DESC
            LIMIT $2
            """,
            cursor_id,
            limit,
        )

    async def list_popular(
        self,
        *,
        window_kind: str,
        cursor_score: Decimal | None,
        cursor_id: int | None,
        limit: int,
    ) -> list[Any]:
        """인기/주간 인기 — 랭킹 스냅샷(워커 계산) 점수/id keyset 순."""
        return await self._connection.fetch(
            f"""
            SELECT {_POST_SELECT}, k.score AS ranking_score
            {_POST_JOINS}
            JOIN community_post_rankings k ON k.post_id = p.id AND k.window_kind = $1
            WHERE p.deleted_at IS NULL AND p.status = 'visible'
              AND (
                $2::numeric IS NULL
                OR k.score < $2
                OR (k.score = $2 AND p.id < $3)
              )
            ORDER BY k.score DESC, p.id DESC
            LIMIT $4
            """,
            window_kind,
            cursor_score,
            cursor_id,
            limit,
        )

    # ---- 관리자 모더레이션 -------------------------------------------------
    async def list_hidden_posts(self, *, limit: int) -> list[Any]:
        """관리자 검토 큐용 숨김 게시글. 공개 응답과 같은 데이터 미니멀리즘을 유지한다."""
        return await self._connection.fetch(
            f"""
            SELECT {_POST_SELECT},
                   reports.report_count,
                   reports.latest_reported_at,
                   reports.report_reasons
            {_POST_JOINS}
            LEFT JOIN LATERAL (
                SELECT
                    count(*)::int AS report_count,
                    max(created_at) AS latest_reported_at,
                    COALESCE(
                        array_remove(array_agg(DISTINCT reason), NULL),
                        ARRAY[]::varchar[]
                    ) AS report_reasons
                FROM community_reports
                WHERE target_type = 'post' AND target_id = p.id
            ) reports ON TRUE
            WHERE p.deleted_at IS NULL AND p.status = 'hidden'
            ORDER BY COALESCE(reports.latest_reported_at, p.updated_at, p.created_at) DESC, p.id DESC
            LIMIT $1
            """,
            limit,
        )

    async def list_hidden_comments(self, *, limit: int) -> list[Any]:
        """관리자 검토 큐용 숨김 댓글."""
        return await self._connection.fetch(
            """
            SELECT c.id, c.post_id, c.parent_comment_id, c.body, c.status,
                   c.created_at, c.updated_at, c.author_user_id,
                   u.member_code AS author_member_code,
                   u.nickname AS author_nickname,
                   p.title AS post_title,
                   reports.report_count,
                   reports.latest_reported_at,
                   reports.report_reasons
              FROM community_comments c
              JOIN users u ON u.id = c.author_user_id
              JOIN community_posts p ON p.id = c.post_id
              LEFT JOIN LATERAL (
                  SELECT
                      count(*)::int AS report_count,
                      max(created_at) AS latest_reported_at,
                      COALESCE(
                          array_remove(array_agg(DISTINCT reason), NULL),
                          ARRAY[]::varchar[]
                      ) AS report_reasons
                  FROM community_reports
                  WHERE target_type = 'comment' AND target_id = c.id
              ) reports ON TRUE
             WHERE c.deleted_at IS NULL
               AND c.status = 'hidden'
               AND p.deleted_at IS NULL
             ORDER BY COALESCE(reports.latest_reported_at, c.updated_at, c.created_at) DESC, c.id DESC
             LIMIT $1
            """,
            limit,
        )

    async def get_hidden_post_for_moderation(self, *, post_id: int) -> Any:
        return await self._connection.fetchrow(
            f"""
            SELECT {_POST_SELECT},
                   reports.report_count,
                   reports.latest_reported_at,
                   reports.report_reasons
            {_POST_JOINS}
            LEFT JOIN LATERAL (
                SELECT
                    count(*)::int AS report_count,
                    max(created_at) AS latest_reported_at,
                    COALESCE(
                        array_remove(array_agg(DISTINCT reason), NULL),
                        ARRAY[]::varchar[]
                    ) AS report_reasons
                FROM community_reports
                WHERE target_type = 'post' AND target_id = p.id
            ) reports ON TRUE
            WHERE p.id = $1 AND p.deleted_at IS NULL AND p.status = 'hidden'
            """,
            post_id,
        )

    async def get_hidden_comment_for_moderation(self, *, comment_id: int) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT c.id, c.post_id, c.parent_comment_id, c.body, c.status,
                   c.created_at, c.updated_at, c.author_user_id,
                   u.member_code AS author_member_code,
                   u.nickname AS author_nickname,
                   p.title AS post_title,
                   reports.report_count,
                   reports.latest_reported_at,
                   reports.report_reasons
              FROM community_comments c
              JOIN users u ON u.id = c.author_user_id
              JOIN community_posts p ON p.id = c.post_id
              LEFT JOIN LATERAL (
                  SELECT
                      count(*)::int AS report_count,
                      max(created_at) AS latest_reported_at,
                      COALESCE(
                          array_remove(array_agg(DISTINCT reason), NULL),
                          ARRAY[]::varchar[]
                      ) AS report_reasons
                  FROM community_reports
                  WHERE target_type = 'comment' AND target_id = c.id
              ) reports ON TRUE
             WHERE c.id = $1
               AND c.deleted_at IS NULL
               AND c.status = 'hidden'
               AND p.deleted_at IS NULL
            """,
            comment_id,
        )

    async def restore_hidden_post(self, *, post_id: int) -> bool:
        restored_id = await self._connection.fetchval(
            """
            UPDATE community_posts
               SET status = 'visible', updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL AND status = 'hidden'
            RETURNING id
            """,
            post_id,
        )
        return restored_id is not None

    async def restore_hidden_comment(self, *, comment_id: int) -> bool:
        restored_id = await self._connection.fetchval(
            """
            UPDATE community_comments
               SET status = 'visible', updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL AND status = 'hidden'
            RETURNING id
            """,
            comment_id,
        )
        return restored_id is not None

    async def delete_hidden_post(self, *, post_id: int) -> bool:
        deleted_id = await self._connection.fetchval(
            """
            UPDATE community_posts
               SET deleted_at = NOW(), updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL AND status = 'hidden'
            RETURNING id
            """,
            post_id,
        )
        return deleted_id is not None

    async def delete_hidden_comment(self, *, comment_id: int) -> bool:
        deleted_id = await self._connection.fetchval(
            """
            UPDATE community_comments
               SET deleted_at = NOW(), updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL AND status = 'hidden'
            RETURNING id
            """,
            comment_id,
        )
        return deleted_id is not None

    async def update_post(
        self,
        *,
        post_id: int,
        author_user_id: int,
        title: str | None,
        body: str | None,
        show_pnl: bool | None,
    ) -> Any:
        """작성자 본인만. 변경된 필드만(COALESCE). 대상 없으면 None."""
        updated_id = await self._connection.fetchval(
            """
            UPDATE community_posts
               SET title = COALESCE($3, title),
                   body = COALESCE($4, body),
                   show_pnl = COALESCE($5, show_pnl),
                   updated_at = NOW()
             WHERE id = $1 AND author_user_id = $2 AND deleted_at IS NULL
            RETURNING id
            """,
            post_id,
            author_user_id,
            title,
            body,
            show_pnl,
        )
        if updated_id is None:
            return None
        return await self.get_post(post_id=updated_id)

    async def soft_delete_post(self, *, post_id: int, author_user_id: int) -> bool:
        """작성자 본인만 soft delete. 대상 없으면 False."""
        deleted_id = await self._connection.fetchval(
            """
            UPDATE community_posts SET deleted_at = NOW()
             WHERE id = $1 AND author_user_id = $2 AND deleted_at IS NULL
            RETURNING id
            """,
            post_id,
            author_user_id,
        )
        return deleted_id is not None

    async def record_view(self, *, post_id: int, viewer_key: str) -> None:
        """조회 중복방지(유저·게시글·당일 1회). 신규 삽입 성공 시에만 view_count 증가."""
        inserted = await self._connection.fetchval(
            """
            INSERT INTO community_post_views (post_id, viewer_key, viewed_on)
            VALUES ($1, $2, CURRENT_DATE)
            ON CONFLICT (post_id, viewer_key, viewed_on) DO NOTHING
            RETURNING id
            """,
            post_id,
            viewer_key,
        )
        if inserted is not None:
            await self._connection.execute(
                "UPDATE community_posts SET view_count = view_count + 1 WHERE id = $1",
                post_id,
            )

    # ---- 댓글 -------------------------------------------------------------
    async def list_comments(self, *, post_id: int) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT c.id, c.post_id, c.parent_comment_id, c.body, c.status, c.created_at,
                   u.member_code AS author_member_code, u.nickname AS author_nickname
              FROM community_comments c
              JOIN users u ON u.id = c.author_user_id
             WHERE c.post_id = $1 AND c.deleted_at IS NULL AND c.status = 'visible'
             ORDER BY c.created_at, c.id
            """,
            post_id,
        )

    async def create_comment(
        self, *, post_id: int, parent_comment_id: int | None, author_user_id: int, body: str
    ) -> Any:
        """1단계 대댓글까지만. 게시글 존재·부모 정합(같은 글·1레벨)을 SQL 에서 강제.

        조건 불충족(글 없음·삭제·부모가 대댓글) 시 삽입 0행 → None 반환(라우트가 판정).
        """
        comment_id = await self._connection.fetchval(
            """
            INSERT INTO community_comments (post_id, parent_comment_id, author_user_id, body)
            SELECT $1, $2, $3, $4
            WHERE EXISTS (
                SELECT 1 FROM community_posts
                 WHERE id = $1 AND deleted_at IS NULL AND status = 'visible'
            )
            AND (
                $2::bigint IS NULL
                OR EXISTS (
                    SELECT 1 FROM community_comments parent
                     WHERE parent.id = $2 AND parent.post_id = $1
                       AND parent.parent_comment_id IS NULL
                       AND parent.deleted_at IS NULL
                )
            )
            RETURNING id
            """,
            post_id,
            parent_comment_id,
            author_user_id,
            body,
        )
        if comment_id is None:
            return None
        return await self._connection.fetchrow(
            """
            SELECT c.id, c.post_id, c.parent_comment_id, c.body, c.status, c.created_at,
                   u.member_code AS author_member_code, u.nickname AS author_nickname
              FROM community_comments c
              JOIN users u ON u.id = c.author_user_id
             WHERE c.id = $1
            """,
            comment_id,
        )

    async def get_comment_author(self, *, comment_id: int) -> int | None:
        return await self._connection.fetchval(
            "SELECT author_user_id FROM community_comments"
            " WHERE id = $1 AND deleted_at IS NULL",
            comment_id,
        )

    async def soft_delete_comment(self, *, comment_id: int, author_user_id: int) -> bool:
        deleted_id = await self._connection.fetchval(
            """
            UPDATE community_comments SET deleted_at = NOW()
             WHERE id = $1 AND author_user_id = $2 AND deleted_at IS NULL
            RETURNING id
            """,
            comment_id,
            author_user_id,
        )
        return deleted_id is not None

    # ---- 반응 -------------------------------------------------------------
    async def toggle_reaction(
        self, *, user_id: int, target_type: str, target_id: int, reaction_type: str
    ) -> str:
        """토글: 있으면 삭제('removed'), 없으면 추가('added'). unique 로 중복 방지."""
        removed = await self._connection.fetchval(
            """
            DELETE FROM community_reactions
             WHERE user_id = $1 AND target_type = $2 AND target_id = $3 AND type = $4
            RETURNING id
            """,
            user_id,
            target_type,
            target_id,
            reaction_type,
        )
        if removed is not None:
            return "removed"
        await self._connection.execute(
            """
            INSERT INTO community_reactions (user_id, target_type, target_id, type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, target_type, target_id, type) DO NOTHING
            """,
            user_id,
            target_type,
            target_id,
            reaction_type,
        )
        return "added"

    async def reaction_count(self, *, target_type: str, target_id: int, reaction_type: str) -> int:
        return await self._connection.fetchval(
            """
            SELECT count(*) FROM community_reactions
             WHERE target_type = $1 AND target_id = $2 AND type = $3
            """,
            target_type,
            target_id,
            reaction_type,
        )

    # ---- 신고 / 모더레이션 -------------------------------------------------
    async def create_report(
        self,
        *,
        reporter_user_id: int,
        target_type: str,
        target_id: int,
        reason: str | None,
        hide_threshold: int,
    ) -> bool:
        """신고 기록(중복 신고 방지). 서로 다른 신고자 수 ≥ 임계면 대상 status='hidden'.

        반환: 이번 호출로 대상이 hidden 으로 전이됐는지 여부.
        """
        await self._connection.execute(
            """
            INSERT INTO community_reports (reporter_user_id, target_type, target_id, reason)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (reporter_user_id, target_type, target_id) DO NOTHING
            """,
            reporter_user_id,
            target_type,
            target_id,
            reason,
        )
        distinct_reporters = await self._connection.fetchval(
            """
            SELECT count(DISTINCT reporter_user_id) FROM community_reports
             WHERE target_type = $1 AND target_id = $2
            """,
            target_type,
            target_id,
        )
        if distinct_reporters is None or distinct_reporters < hide_threshold:
            return False
        table = "community_posts" if target_type == "post" else "community_comments"
        hidden_id = await self._connection.fetchval(
            f"""
            UPDATE {table} SET status = 'hidden'
             WHERE id = $1 AND status = 'visible'
            RETURNING id
            """,
            target_id,
        )
        return hidden_id is not None
