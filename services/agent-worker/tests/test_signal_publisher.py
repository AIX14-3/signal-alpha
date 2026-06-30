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
    def __init__(self, columns_by_table=None):
        self.executed = []  # (table-ish sql, rows)
        # 테이블별 실존 컬럼 맵. 맵에 없거나 None → 컬럼 조회 빈 결과(=백엔드에 테이블 없음).
        # M6 이후 빈 결과인 테이블에 복사할 행이 있으면 publisher 가 명시적 RuntimeError 를 던진다.
        self._columns_by_table = columns_by_table

    async def fetch(self, sql, table):
        # _backend_columns 의 pg_attribute 조회 대역.
        if self._columns_by_table is None:
            return []
        return [{"attname": c} for c in self._columns_by_table.get(table, [])]

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
    backend = _FakeBackend(
        columns_by_table={
            "stocks": ["id", "ticker"],
            "final_signals": ["id", "stock_id", "ml_direction"],
        }
    )

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


def test_publish_drops_columns_absent_in_backend_schema():
    # 수집 DB final_signals 에 source_predictions 가 있으나 백엔드는 아직 그 마이그를
    # 못 받은 드리프트 상황. 옛 동작은 INSERT 가 "column does not exist" 로 종목 발행을
    # 통째로 실패시켰다 — 이제 누락 컬럼만 건너뛰고 나머지는 정상 복사해야 한다.
    source = _FakeSource(
        {
            "stocks": [{"id": 1, "ticker": "005930"}],
            "final_signals": [
                {
                    "id": 85,
                    "stock_id": 1,
                    "ml_direction": "positive",
                    "source_predictions": {"SRC": {"final_score": 0.6}},
                }
            ],
        }
    )
    backend = _FakeBackend(
        columns_by_table={
            "stocks": ["id", "ticker"],
            "final_signals": ["id", "stock_id", "ml_direction"],  # source_predictions 없음
        }
    )

    counts = asyncio.run(publish_stock(source, backend, stock_id=1))

    assert counts["final_signals"] == 1  # 전체 페일이 아니라 행은 복사됨
    fs_sql, fs_args = backend.executed[1]
    assert "INSERT INTO final_signals" in fs_sql
    assert "source_predictions" not in fs_sql  # 백엔드에 없는 컬럼은 빠짐
    assert '"ml_direction"' in fs_sql
    assert len(fs_args[0]) == 3  # id, stock_id, ml_direction (source_predictions 제외)


def test_publish_falls_back_when_backend_missing_id_column():
    # id(ON CONFLICT 키)가 빠지는 건 부가 컬럼 부재가 아니라 구조적 드리프트 → 우아한 강등
    # 대상이 아니다. 원본 컬럼으로 폴백해(실 DB 라면 INSERT 가 시끄럽게 실패) 조용한 빈/부분
    # INSERT 를 만들지 않는다.
    source = _FakeSource(
        {"final_signals": [{"id": 85, "stock_id": 1, "ml_direction": "positive"}]}
    )
    backend = _FakeBackend(columns_by_table={"final_signals": ["stock_id", "ml_direction"]})  # id 없음

    asyncio.run(publish_stock(source, backend, stock_id=1))

    fs_sql = backend.executed[0][0]  # 이 source 는 final_signals 만 비어있지 않음
    assert "INSERT INTO final_signals" in fs_sql
    assert '"id"' in fs_sql  # id 를 드롭하지 않고 유지
    assert "ON CONFLICT (id)" in fs_sql
