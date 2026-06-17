from __future__ import annotations

from typing import Any

# L2 지분·내부자 이벤트 적재 리포지토리. (DartFinancialFactsRepository 와 동일 패턴)
#
# 멱등 자연키: (corp_code, rcept_no, holder_name, holder_type, line_seq) — 마이그레이션 005 의
# uq_ownership_event. line_seq 는 한 보고서에서 같은 보고자가 여러 행(증권종류/변동내역)으로
# 나뉠 때 행이 서로 덮어쓰지 않도록 하는 행 일련번호다(수집기가 부여, 미지정 시 0).
# 정정 단조성: 이미 적재된 행보다 rcept_no 가 같거나 큰 경우에만 갱신한다
# (과거 재수집이 최신 정정을 덮어쓰지 않도록).

_REQUIRED_KEYS = ("corp_code", "rcept_no", "report_date", "holder_name", "holder_type")

_INSERT_COLUMNS = """
    stock_id, corp_code, rcept_no, line_seq, report_date, holder_name, holder_type,
    shares, ratio, shares_delta, ratio_delta, report_reason
"""

_CONFLICT_TARGET = "(corp_code, rcept_no, holder_name, holder_type, line_seq)"

_DO_UPDATE = """
    DO UPDATE SET
        stock_id = EXCLUDED.stock_id,
        report_date = EXCLUDED.report_date,
        shares = EXCLUDED.shares,
        ratio = EXCLUDED.ratio,
        shares_delta = EXCLUDED.shares_delta,
        ratio_delta = EXCLUDED.ratio_delta,
        report_reason = EXCLUDED.report_reason,
        fetched_at = NOW(),
        updated_at = NOW()
    WHERE dart_ownership_events.rcept_no <= EXCLUDED.rcept_no
"""


class DartOwnershipRepository:
    """``dart_ownership_events`` 적재/조회 리포지토리."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_event(
        self,
        *,
        corp_code: str,
        rcept_no: str,
        report_date: Any,
        holder_name: str,
        holder_type: str,
        line_seq: int = 0,
        shares: int | None = None,
        ratio: float | None = None,
        shares_delta: int | None = None,
        ratio_delta: float | None = None,
        report_reason: str | None = None,
        stock_id: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            f"""
            INSERT INTO dart_ownership_events ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT {_CONFLICT_TARGET}
            {_DO_UPDATE}
            RETURNING *
            """,
            stock_id,
            corp_code.strip(),
            rcept_no.strip(),
            int(line_seq),
            report_date,
            holder_name.strip(),
            holder_type.strip(),
            shares,
            ratio,
            shares_delta,
            ratio_delta,
            report_reason,
        )

    async def upsert_events(self, entries: list[dict[str, Any]]) -> int:
        """지분 이벤트 일괄 멱등 적재. 필수키 누락 행은 건너뛴다. 반환=시도 행 수."""
        rows = [
            (
                entry.get("stock_id"),
                str(entry["corp_code"]).strip(),
                str(entry["rcept_no"]).strip(),
                int(entry.get("line_seq") or 0),
                entry["report_date"],
                str(entry["holder_name"]).strip(),
                str(entry["holder_type"]).strip(),
                entry.get("shares"),
                entry.get("ratio"),
                entry.get("shares_delta"),
                entry.get("ratio_delta"),
                entry.get("report_reason"),
            )
            for entry in entries
            if all(entry.get(key) not in (None, "") for key in _REQUIRED_KEYS)
        ]
        if not rows:
            return 0

        await self._connection.executemany(
            f"""
            INSERT INTO dart_ownership_events ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT {_CONFLICT_TARGET}
            {_DO_UPDATE}
            """,
            rows,
        )
        return len(rows)

    async def list_by_corp(self, corp_code: str) -> Any:
        return await self._connection.fetch(
            """
            SELECT *
            FROM dart_ownership_events
            WHERE corp_code = $1
            ORDER BY report_date DESC, holder_type, holder_name, id
            """,
            corp_code.strip(),
        )

    async def get_latest_rcept_no(
        self,
        *,
        corp_code: str,
        holder_type: str,
    ) -> Any:
        return await self._connection.fetchval(
            """
            SELECT MAX(rcept_no)
            FROM dart_ownership_events
            WHERE corp_code = $1 AND holder_type = $2
            """,
            corp_code.strip(),
            holder_type.strip(),
        )
