from __future__ import annotations

from typing import Any

# L3 임직원 현황 적재 리포지토리. (DartFinancialFactsRepository / DartOwnershipRepository 동일 패턴)
#
# 멱등 자연키: (corp_code, bsns_year, reprt_code, COALESCE(segment,''), COALESCE(sex,''), line_seq) —
# 마이그레이션 013 의 uq_employee_stats. empSttus 는 사업부문(segment)×성별(sex) 로 행이
# 쪼개지므로 두 차원을 자연키에 포함한다(빈값은 '' 폴백 — NULL distinct 회피, L1 006 동일).
# line_seq 는 같은 (segment, sex) 가 한 보고서에서 여러 행으로 나뉠 때 행이 서로 덮어쓰지
# 않도록 하는 행 일련번호다(수집기가 부여, 미지정 시 0 — L2 011 동일).
# 정정 단조성: 이미 적재된 행보다 rcept_no 가 같거나 큰 경우에만 갱신한다
# (과거 재수집이 최신 정정을 덮어쓰지 않도록).

_REQUIRED_KEYS = ("corp_code", "rcept_no", "bsns_year", "reprt_code")

_INSERT_COLUMNS = """
    stock_id, corp_code, rcept_no, line_seq, bsns_year, reprt_code, segment, sex,
    headcount, regular_count, contract_count, avg_tenure_years,
    avg_salary_krw, salary_total_krw
"""

# COALESCE 표현식 유니크 인덱스 대상(uq_employee_stats) — ON CONFLICT 추론용 컬럼식.
_CONFLICT_TARGET = (
    "(corp_code, bsns_year, reprt_code, COALESCE(segment, ''), COALESCE(sex, ''), line_seq)"
)

_DO_UPDATE = """
    DO UPDATE SET
        stock_id = EXCLUDED.stock_id,
        headcount = EXCLUDED.headcount,
        regular_count = EXCLUDED.regular_count,
        contract_count = EXCLUDED.contract_count,
        avg_tenure_years = EXCLUDED.avg_tenure_years,
        avg_salary_krw = EXCLUDED.avg_salary_krw,
        salary_total_krw = EXCLUDED.salary_total_krw,
        rcept_no = EXCLUDED.rcept_no,
        fetched_at = NOW(),
        updated_at = NOW()
    WHERE dart_employee_stats.rcept_no <= EXCLUDED.rcept_no
"""


class DartEmployeeStatsRepository:
    """``dart_employee_stats`` 적재/조회 리포지토리."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_stat(
        self,
        *,
        corp_code: str,
        rcept_no: str,
        bsns_year: int,
        reprt_code: str,
        line_seq: int = 0,
        segment: str | None = None,
        sex: str | None = None,
        headcount: int | None = None,
        regular_count: int | None = None,
        contract_count: int | None = None,
        avg_tenure_years: float | None = None,
        avg_salary_krw: int | None = None,
        salary_total_krw: int | None = None,
        stock_id: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            f"""
            INSERT INTO dart_employee_stats ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT {_CONFLICT_TARGET}
            {_DO_UPDATE}
            RETURNING *
            """,
            stock_id,
            corp_code.strip(),
            rcept_no.strip(),
            int(line_seq),
            int(bsns_year),
            reprt_code.strip(),
            segment,
            sex,
            headcount,
            regular_count,
            contract_count,
            avg_tenure_years,
            avg_salary_krw,
            salary_total_krw,
        )

    async def upsert_stats(self, entries: list[dict[str, Any]]) -> int:
        """임직원 현황 일괄 멱등 적재. 필수키 누락 행은 건너뛴다. 반환=시도 행 수."""
        rows = [
            (
                entry.get("stock_id"),
                str(entry["corp_code"]).strip(),
                str(entry["rcept_no"]).strip(),
                int(entry.get("line_seq") or 0),
                int(entry["bsns_year"]),
                str(entry["reprt_code"]).strip(),
                entry.get("segment"),
                entry.get("sex"),
                entry.get("headcount"),
                entry.get("regular_count"),
                entry.get("contract_count"),
                entry.get("avg_tenure_years"),
                entry.get("avg_salary_krw"),
                entry.get("salary_total_krw"),
            )
            for entry in entries
            if all(entry.get(key) not in (None, "") for key in _REQUIRED_KEYS)
        ]
        if not rows:
            return 0

        await self._connection.executemany(
            f"""
            INSERT INTO dart_employee_stats ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
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
            FROM dart_employee_stats
            WHERE corp_code = $1
            ORDER BY bsns_year DESC, reprt_code, segment, sex, id
            """,
            corp_code.strip(),
        )

    async def list_for_normalization(self, *, stock_id: int, limit: int = 500) -> list[Any]:
        return await self._connection.fetch(
            """
            SELECT *
            FROM dart_employee_stats
            WHERE stock_id = $1
            ORDER BY bsns_year DESC, reprt_code DESC, segment, sex, line_seq, id
            LIMIT $2
            """,
            int(stock_id),
            int(limit),
        )

    async def get_latest_rcept_no(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
    ) -> Any:
        return await self._connection.fetchval(
            """
            SELECT MAX(rcept_no)
            FROM dart_employee_stats
            WHERE corp_code = $1 AND bsns_year = $2 AND reprt_code = $3
            """,
            corp_code.strip(),
            int(bsns_year),
            reprt_code.strip(),
        )
