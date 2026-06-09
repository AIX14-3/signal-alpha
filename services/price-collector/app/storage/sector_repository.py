"""Persistence for collected sector index data.

The sector collector owns ``sectors`` (read for the active universe) and
``sector_ohlcv`` (write), and tracks each batch in ``collector_runs``
(collector_type = 'PRICE'), consistent with the price domain.
"""

from typing import Protocol

from app.schemas.sector import SectorOhlcvRow, SectorRef


class SectorRepository(Protocol):
    def list_active_sectors(self) -> list[SectorRef]:
        """Return active sectors (incl. market indices) to collect."""

    def upsert_sector_ohlcv(self, rows: list[SectorOhlcvRow]) -> int:
        """Insert or update sector_ohlcv rows; return the number written."""

    def start_run(self, run_mode: str) -> int:
        """Open a ``collector_runs`` row and return its id."""

    def finish_run(
        self,
        run_id: int,
        status: str,
        collected_count: int,
        inserted_count: int,
        failed_count: int,
        error_message: str | None = None
    ) -> None:
        """Close a ``collector_runs`` row with final counts."""


_UPSERT_SQL = """
INSERT INTO sector_ohlcv (
    sector_id, trade_date, open, high, low, close,
    volume, trading_value, change_pct
) VALUES (
    %(sector_id)s, %(trade_date)s, %(open)s, %(high)s, %(low)s, %(close)s,
    %(volume)s, %(trading_value)s, %(change_pct)s
)
ON CONFLICT (sector_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = COALESCE(EXCLUDED.volume, sector_ohlcv.volume),
    trading_value =
        COALESCE(EXCLUDED.trading_value, sector_ohlcv.trading_value),
    change_pct = COALESCE(EXCLUDED.change_pct, sector_ohlcv.change_pct)
"""


class PostgresSectorRepository:
    """``psycopg`` (v3) backed repository against the shared PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        import psycopg  # imported here so the module loads without the driver

        self._conn = psycopg.connect(database_url, autocommit=False)

    def list_active_sectors(self) -> list[SectorRef]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, kiwoom_code, market
                FROM sectors
                WHERE is_active = TRUE
                ORDER BY market, kiwoom_code
                """
            )
            rows = cur.fetchall()
        return [
            SectorRef(id=int(row[0]), kiwoom_code=row[1], market=row[2])
            for row in rows
        ]

    def upsert_sector_ohlcv(self, rows: list[SectorOhlcvRow]) -> int:
        if not rows:
            return 0
        params = [
            {
                "sector_id": row.sector_id,
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "trading_value": row.trading_value,
                "change_pct": row.change_pct
            }
            for row in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, params)
        self._conn.commit()
        return len(params)

    def start_run(self, run_mode: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO collector_runs (collector_type, run_mode, status)
                VALUES ('PRICE', %s, 'running')
                RETURNING id
                """,
                (run_mode,)
            )
            run_id = int(cur.fetchone()[0])
        self._conn.commit()
        return run_id

    def finish_run(
        self,
        run_id: int,
        status: str,
        collected_count: int,
        inserted_count: int,
        failed_count: int,
        error_message: str | None = None
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE collector_runs SET
                    status = %s,
                    finished_at = NOW(),
                    collected_count = %s,
                    inserted_count = %s,
                    failed_count = %s,
                    error_message = %s
                WHERE id = %s
                """,
                (
                    status,
                    collected_count,
                    inserted_count,
                    failed_count,
                    error_message,
                    run_id
                )
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
