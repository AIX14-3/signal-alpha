"""커뮤니티 인기 랭킹 배치 — 가중합/롤링 윈도우/멱등 upsert/prune (fake 연결)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from app.publish.community_rankings import (
    DEFAULT_WEIGHTS,
    WINDOWS,
    Weights,
    compute_score,
    record_community_rankings,
)

_NOW = datetime.fromisoformat("2026-07-07T12:00:00+09:00")


def _row(post_id, likes=0, comments=0, views=0):
    return {"post_id": post_id, "likes": likes, "comments": comments, "views": views}


class _FakeConn:
    """윈도우별 집계 결과 대역 + upsert/prune 기록. cutoff=None → 'all' 응답."""

    def __init__(self, weekly_rows, all_rows, existing=()):
        self._weekly = weekly_rows
        self._all = all_rows
        self.fetch_cutoffs = []  # 윈도우별로 넘어온 cutoff
        self.upserts = []  # (post_id, window_kind, score, likes, comments, views)
        self.prunes = []  # (window_kind, active_ids)
        # 윈도우별 현재 테이블에 존재하는 post_id 집합
        self._existing = {"weekly": set(existing), "all": set(existing)}

    async def fetch(self, sql, cutoff):
        assert "FROM community_posts p" in sql
        assert "type = 'like'" in sql and "status = 'visible'" in sql
        self.fetch_cutoffs.append(cutoff)
        return list(self._all if cutoff is None else self._weekly)

    async def execute(self, sql, *args):
        if "INSERT INTO community_post_rankings" in sql:
            assert "ON CONFLICT (post_id, window_kind) DO UPDATE" in sql
            self.upserts.append(args)
            self._existing[args[1]].add(args[0])
            return "INSERT 0 1"
        if "DELETE FROM community_post_rankings" in sql:
            window_kind, active_ids = args
            self.prunes.append((window_kind, list(active_ids)))
            removed = self._existing[window_kind] - set(active_ids)
            self._existing[window_kind] -= removed
            return f"DELETE {len(removed)}"
        raise AssertionError(f"unexpected SQL: {sql[:60]}")


def test_windows_match_db_check_constraint():
    assert WINDOWS == (("weekly", 7), ("all", None))


def test_compute_score_weighted_sum():
    # 2 likes*3 + 1 comment*5 + 10 views*1 = 6 + 5 + 10 = 21
    assert compute_score(2, 1, 10) == 21
    assert compute_score(0, 0, 0) == 0


def test_compute_score_custom_weights():
    assert compute_score(1, 1, 1, Weights(like=10, comment=20, view=1)) == 31


def test_weekly_cutoff_is_now_minus_7_days_all_is_none():
    conn = _FakeConn(weekly_rows=[], all_rows=[])
    asyncio.run(record_community_rankings(conn, now=_NOW))
    # WINDOWS 순서: weekly(cutoff=now-7d) → all(cutoff=None)
    assert conn.fetch_cutoffs == [_NOW - timedelta(days=7), None]


def test_upserts_score_per_window():
    weekly = [_row(10, likes=2, comments=1, views=10)]  # score 21
    all_rows = [_row(10, likes=5, comments=3, views=40)]  # score 5*3+3*5+40 = 70
    conn = _FakeConn(weekly, all_rows)

    stats = asyncio.run(record_community_rankings(conn, now=_NOW))

    assert stats.windows == 2 and stats.upserts == 2 and stats.failed == 0
    by_window = {u[1]: u for u in conn.upserts}
    assert by_window["weekly"][0] == 10 and by_window["weekly"][2] == Decimal(21)
    assert by_window["weekly"][3:] == (2, 1, 10)
    assert by_window["all"][2] == Decimal(70)
    assert by_window["all"][3:] == (5, 3, 40)


def test_empty_feed_prunes_whole_window():
    # 노출 게시글 0 → active_ids 빈 배열 → 기존 스냅샷 전부 제거.
    conn = _FakeConn(weekly_rows=[], all_rows=[], existing=[99, 100])
    stats = asyncio.run(record_community_rankings(conn, now=_NOW))

    assert stats.upserts == 0
    assert conn.prunes == [("weekly", []), ("all", [])]
    assert stats.pruned == 4  # 윈도우당 2행 제거 × 2윈도우


def test_prune_removes_hidden_post_keeps_visible():
    # 게시글 10 만 노출, 99 는 숨김(집계서 빠짐) → 99 스냅샷만 제거.
    conn = _FakeConn(
        weekly_rows=[_row(10, likes=1)],
        all_rows=[_row(10, likes=1)],
        existing=[10, 99],
    )
    asyncio.run(record_community_rankings(conn, now=_NOW))

    for window_kind, active_ids in conn.prunes:
        assert active_ids == [10]
        assert 99 not in conn._existing[window_kind]
        assert 10 in conn._existing[window_kind]


def test_idempotent_upsert_overwrites_same_key():
    row = [_row(10, likes=1, comments=0, views=0)]
    conn = _FakeConn(weekly_rows=row, all_rows=row, existing=[10])

    asyncio.run(record_community_rankings(conn, now=_NOW))

    # 재실행해도 (post,window) 유일 — 살아있는 10 은 prune 되지 않는다.
    assert conn.prunes == [("weekly", [10]), ("all", [10])]
    assert all(10 in conn._existing[w] for w in ("weekly", "all"))


def test_isolates_per_post_upsert_failure():
    class _Exploding(_FakeConn):
        async def execute(self, sql, *args):
            if "INSERT" in sql and args[0] == 10:
                raise RuntimeError("boom")
            return await super().execute(sql, *args)

    rows = [_row(10, likes=1), _row(11, likes=2)]
    conn = _Exploding(weekly_rows=rows, all_rows=rows)

    stats = asyncio.run(record_community_rankings(conn, now=_NOW))

    # 10 두 윈도우 실패, 11 두 윈도우 성공.
    assert stats.failed == 2 and stats.upserts == 2
    assert all(u[0] == 11 for u in conn.upserts)
    # upsert 실패해도 살아있는 게시글(10)은 prune 보호 대상 — active_ids 에 남는다.
    assert conn.prunes == [("weekly", [10, 11]), ("all", [10, 11])]


def test_isolates_window_aggregate_failure():
    class _AggFails(_FakeConn):
        async def fetch(self, sql, cutoff):
            if cutoff is not None:  # weekly 만 폭발
                raise RuntimeError("agg boom")
            return await super().fetch(sql, cutoff)

    all_rows = [_row(10, likes=1)]
    conn = _AggFails(weekly_rows=[], all_rows=all_rows)

    stats = asyncio.run(record_community_rankings(conn, now=_NOW))

    # weekly 집계 실패 → all 만 정상 처리.
    assert stats.failed == 1 and stats.windows == 1 and stats.upserts == 1
    assert conn.upserts[0][1] == "all"
    assert any("window=weekly aggregate" in e for e in stats.errors)


def test_default_weights_values():
    assert (DEFAULT_WEIGHTS.like, DEFAULT_WEIGHTS.comment, DEFAULT_WEIGHTS.view) == (3, 5, 1)
