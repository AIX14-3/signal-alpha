"""앱레벨 발행 publisher — 수집 DB → 백엔드 DB (#525/#531 WS-C/#11).

물리 2-DB 분리에서 백엔드/프론트는 워커 산출물을 직접 JOIN 할 수 없다(cross-DB JOIN 불가).
그래서 워커가 종목의 **발행 산출물**(PUBLISHED 테이블)을 백엔드 DB 로 앱레벨 복사한다 —
기존 ``api.*`` 읽기 view 를 대체하는 발행 계약.

발행 대상 = ``db_partition.PUBLISHED_TABLES`` 와 동일 집합. 백엔드 published 테이블 간 내부
FK(final_signals→analysis_results→stocks, signal_events→source_documents …)를 보존하기 위해
**부모→자식 FK 순서**로 복사하고, 원본 ``id`` 를 그대로 보존해(ON CONFLICT(id) UPSERT) 참조
무결성을 유지한다. 멱등 — 재발행 시 최신 행으로 갱신.

순수 SQL·연결 주입형(asyncpg Connection 2개: source=수집, backend=백엔드)이라 fake 로
단위테스트 가능하고, 운영에선 두 풀에서 각각 acquire 해 호출한다.
"""

from __future__ import annotations

from typing import Any, Sequence

# 부모 → 자식 FK 순서. db_partition.PUBLISHED_TABLES 와 일치(순서만 의존성 기준).
PUBLISH_ORDER: tuple[str, ...] = (
    "stocks",
    "source_documents",
    "analysis_results",
    "signal_events",
    "agent_results",
    "final_signals",
)

# 종목 필터 컬럼 — stocks 는 PK(id), 나머지는 stock_id.
_FILTER_COLUMN: dict[str, str] = {"stocks": "id"}


def filter_column(table: str) -> str:
    return _FILTER_COLUMN.get(table, "stock_id")


def build_upsert_sql(table: str, columns: Sequence[str]) -> str:
    """원본 id 보존 ON CONFLICT(id) UPSERT 문. 전 컬럼을 EXCLUDED 로 갱신(id 제외)."""
    collist = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c != "id")
    on_conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    return f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) ON CONFLICT (id) {on_conflict}"


async def _read_rows(source_conn: Any, table: str, stock_id: int) -> list[Any]:
    column = filter_column(table)
    return list(await source_conn.fetch(f"SELECT * FROM {table} WHERE {column} = $1", stock_id))


async def _upsert_rows(backend_conn: Any, table: str, rows: Sequence[Any]) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    sql = build_upsert_sql(table, columns)
    await backend_conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])
    return len(rows)


async def publish_stock(
    source_conn: Any,
    backend_conn: Any,
    *,
    stock_id: int,
) -> dict[str, int]:
    """종목의 발행 산출물을 수집 DB → 백엔드 DB 로 FK 순서 복사. {table: 행수} 반환.

    백엔드 published 테이블 간 내부 FK 보존을 위해 한 트랜잭션으로 묶어 호출하는 것을 권장
    (호출자가 backend_conn.transaction() 컨텍스트에서 호출).
    """
    published: dict[str, int] = {}
    for table in PUBLISH_ORDER:
        rows = await _read_rows(source_conn, table, stock_id)
        published[table] = await _upsert_rows(backend_conn, table, rows)
    return published
