"""앱레벨 발행 publisher (#11) — 순수 SQL 빌더 + FK 순서 복사(fake 연결)."""

from __future__ import annotations

import asyncio

from app.publish.signal_publisher import (
    PUBLISH_ORDER,
    build_upsert_sql,
    filter_column,
    publish_stock,
)


def test_publish_order_is_fk_parent_first():
    # 부모가 자식보다 먼저(외래키 위반 방지).
    order = list(PUBLISH_ORDER)
    assert order.index("stocks") < order.index("final_signals")
    assert order.index("analysis_results") < order.index("final_signals")
    assert order.index("analysis_results") < order.index("agent_results")
    assert order.index("source_documents") < order.index("signal_events")


def test_filter_column():
    assert filter_column("stocks") == "id"
    assert filter_column("final_signals") == "stock_id"
    assert filter_column("signal_events") == "stock_id"


def test_build_upsert_sql_preserves_id_and_updates_rest():
    sql = build_upsert_sql("final_signals", ["id", "stock_id", "ml_direction"])
    assert 'INSERT INTO final_signals ("id", "stock_id", "ml_direction")' in sql
    assert "VALUES ($1, $2, $3)" in sql
    assert "ON CONFLICT (id) DO UPDATE SET" in sql
    assert '"stock_id" = EXCLUDED."stock_id"' in sql
    assert '"ml_direction" = EXCLUDED."ml_direction"' in sql
    assert '"id" = EXCLUDED."id"' not in sql  # id 는 갱신 대상 아님


class _FakeSource:
    """stock 별 published 행을 돌려주는 수집 DB 대역."""

    def __init__(self, rows_by_table):
        self._rows = rows_by_table

    async def fetch(self, sql, stock_id):
        for table, rows in self._rows.items():
            if f"FROM {table} " in sql:
                return rows
        return []


class _FakeBackend:
    def __init__(self):
        self.executed = []  # (table-ish sql, rows)

    async def executemany(self, sql, args_list):
        self.executed.append((sql, args_list))


def test_publish_stock_copies_in_order_and_skips_empty():
    source = _FakeSource(
        {
            "stocks": [{"id": 1, "ticker": "005930"}],
            "final_signals": [
                {"id": 85, "stock_id": 1, "ml_direction": "positive"},
                {"id": 84, "stock_id": 1, "ml_direction": None},
            ],
            # 나머지 테이블은 빈 결과 → executemany 미호출.
        }
    )
    backend = _FakeBackend()

    counts = asyncio.run(publish_stock(source, backend, stock_id=1))

    assert counts["stocks"] == 1
    assert counts["final_signals"] == 2
    assert counts["analysis_results"] == 0  # 빈 테이블
    # 빈 테이블은 executemany 호출하지 않음 → stocks + final_signals 2건만.
    assert len(backend.executed) == 2
    # 첫 호출이 stocks(FK 부모) 여야 한다.
    assert "INSERT INTO stocks" in backend.executed[0][0]
    assert "INSERT INTO final_signals" in backend.executed[1][0]
    assert len(backend.executed[1][1]) == 2  # final_signals 2행
