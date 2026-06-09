"""Persistence for collected price data.

The collector owns the ``ohlcv_data`` table and tracks each batch in
``collector_runs`` (collector_type = 'PRICE'), per the DB design's table
responsibility matrix.
"""

from typing import Protocol

from app.schemas.price import OhlcvRow


class OhlcvRepository(Protocol):
    def resolve_stock_id(self, ticker: str) -> int | None:
        """Map a ticker to ``stocks.id`` (None when the stock is unknown)."""

    def upsert_ohlcv(self, stock_id: int, rows: list[OhlcvRow]) -> int:
        """Insert or update ohlcv rows; return the number written."""

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
INSERT INTO ohlcv_data (
    stock_id, trade_date, open, high, low, close, volume,
    foreign_net, institution_net, change_pct, market_cap
) VALUES (
    %(stock_id)s, %(trade_date)s, %(open)s, %(high)s, %(low)s, %(close)s,
    %(volume)s, %(foreign_net)s, %(institution_net)s, %(change_pct)s,
    %(market_cap)s
)
ON CONFLICT (stock_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    foreign_net = COALESCE(EXCLUDED.foreign_net, ohlcv_data.foreign_net),
    institution_net =
        COALESCE(EXCLUDED.institution_net, ohlcv_data.institution_net),
    change_pct = COALESCE(EXCLUDED.change_pct, ohlcv_data.change_pct),
    market_cap = COALESCE(EXCLUDED.market_cap, ohlcv_data.market_cap)
"""


class PostgresOhlcvRepository:
    """``psycopg`` (v3) backed repository against the shared PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        import psycopg  # imported here so the module loads without the driver

        self._conn = psycopg.connect(database_url, autocommit=False)

    def resolve_stock_id(self, ticker: str) -> int | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT id FROM stocks WHERE ticker = %s", (ticker,))
            row = cur.fetchone()
        return int(row[0]) if row else None

    def upsert_ohlcv(self, stock_id: int, rows: list[OhlcvRow]) -> int:
        if not rows:
            return 0
        params = [
            {
                "stock_id": stock_id,
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "foreign_net": row.foreign_net,
                "institution_net": row.institution_net,
                "change_pct": row.change_pct,
                "market_cap": row.market_cap
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
