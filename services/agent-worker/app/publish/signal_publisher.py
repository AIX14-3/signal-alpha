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

import logging
from typing import Any, Sequence

logger = logging.getLogger(__name__)

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


async def _backend_columns(backend_conn: Any, table: str) -> set[str]:
    """백엔드의 ``table`` 실제 컬럼 집합(search_path 해석). 미존재 테이블이면 빈 집합."""
    rows = await backend_conn.fetch(
        "SELECT attname FROM pg_attribute "
        "WHERE attrelid = to_regclass($1) AND attnum > 0 AND NOT attisdropped",
        table,
    )
    return {row["attname"] for row in (rows or [])}


async def _upsert_rows(backend_conn: Any, table: str, rows: Sequence[Any]) -> int:
    if not rows:
        return 0
    source_columns = list(rows[0].keys())
    # 백엔드에 실제로 존재하는 컬럼만 복사한다. 수집 DB 가 추가 마이그(예: source_predictions)를
    # 먼저 받고 백엔드가 아직이면, 옛 동작은 INSERT 가 "column ... does not exist" 로 전체 발행을
    # 종목 단위로 하드페일시켰다(마이그 윈도우 동안 모든 발행 차단). 누락 컬럼은 건너뛰고 경고만
    # 남겨 우아하게 강등한다(백엔드가 마이그를 받으면 다음 발행부터 자동 포함). 컬럼 조회가 비면
    # (테이블 자체 미존재 등) 기존처럼 원본 컬럼으로 진행해 진짜 결손은 시끄럽게 실패시킨다.
    backend_columns = await _backend_columns(backend_conn, table)
    columns = [c for c in source_columns if c in backend_columns] if backend_columns else source_columns
    # 우아한 강등은 **부가(nullable) 컬럼 부재**만 대상. id(ON CONFLICT 키)가 빠지면 SQL 이
    # 깨지고(`ON CONFLICT (id)` 참조 불가/빈 컬럼 INSERT), 그건 부가가 아닌 구조적 드리프트다 →
    # 원본 컬럼으로 되돌려 시끄럽게 실패시킨다(조용한 부분 발행 금지). NOT NULL 컬럼이 빠진 경우도
    # INSERT 가 NOT NULL 위반으로 시끄럽게 실패하므로(부가가 아님) 의도대로 막힌다.
    if "id" not in columns:
        columns = source_columns
    dropped = [c for c in source_columns if c not in columns]
    if dropped:
        logger.warning(
            "publish: %s 의 백엔드 스키마에 없는 컬럼 %s 를 건너뜀(마이그 드리프트?) — "
            "백엔드 마이그 적용 시 다음 발행부터 자동 포함",
            table,
            dropped,
        )
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
