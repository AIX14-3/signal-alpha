from __future__ import annotations

from typing import Any

# L1 정형 재무 fact 적재 리포지토리. (DartRepository 와 동일 패턴)
#
# 멱등 자연키: (corp_code, bsns_year, reprt_code, fs_div, sj_div,
#              COALESCE(account_id, account_nm)) — 마이그레이션 003 의 uq_dart_fin_fact.
# 정정 단조성: 이미 적재된 행보다 rcept_no 가 같거나 큰 경우에만 갱신한다(과거 재수집이
# 최신 정정을 덮어쓰지 않도록).

_REQUIRED_KEYS = ("corp_code", "rcept_no", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_nm")

_INSERT_COLUMNS = """
    stock_id, corp_code, rcept_no, bsns_year, reprt_code, fs_div, sj_div,
    account_id, account_nm, amount_krw, amount_raw, currency, period_label, fiscal_period
"""

_CONFLICT_TARGET = (
    "(corp_code, bsns_year, reprt_code, fs_div, sj_div, COALESCE(account_id, account_nm))"
)

_DO_UPDATE = """
    DO UPDATE SET
        stock_id = EXCLUDED.stock_id,
        rcept_no = EXCLUDED.rcept_no,
        account_id = EXCLUDED.account_id,
        account_nm = EXCLUDED.account_nm,
        amount_krw = EXCLUDED.amount_krw,
        amount_raw = EXCLUDED.amount_raw,
        currency = EXCLUDED.currency,
        period_label = EXCLUDED.period_label,
        fiscal_period = EXCLUDED.fiscal_period,
        fetched_at = NOW(),
        updated_at = NOW()
    WHERE dart_financial_facts.rcept_no <= EXCLUDED.rcept_no
"""


class DartFinancialFactsRepository:
    """``dart_financial_facts`` 적재/조회 리포지토리."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_fact(
        self,
        *,
        corp_code: str,
        rcept_no: str,
        bsns_year: int,
        reprt_code: str,
        fs_div: str,
        sj_div: str,
        account_nm: str,
        account_id: str | None = None,
        amount_krw: int | None = None,
        amount_raw: str | None = None,
        currency: str = "KRW",
        period_label: str | None = None,
        fiscal_period: str | None = None,
        stock_id: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            f"""
            INSERT INTO dart_financial_facts ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT {_CONFLICT_TARGET}
            {_DO_UPDATE}
            RETURNING *
            """,
            stock_id,
            corp_code.strip(),
            rcept_no.strip(),
            int(bsns_year),
            reprt_code.strip(),
            fs_div.strip(),
            sj_div.strip(),
            account_id.strip() if account_id else None,
            account_nm.strip(),
            amount_krw,
            amount_raw,
            (currency or "KRW").strip(),
            (period_label or f"{int(bsns_year)}{reprt_code}").strip(),
            fiscal_period,
        )

    async def upsert_facts(self, entries: list[dict[str, Any]]) -> int:
        """계정 fact 일괄 멱등 적재. 필수키 누락 행은 건너뛴다. 반환=시도 행 수."""
        rows = [
            (
                entry.get("stock_id"),
                str(entry["corp_code"]).strip(),
                str(entry["rcept_no"]).strip(),
                int(entry["bsns_year"]),
                str(entry["reprt_code"]).strip(),
                str(entry["fs_div"]).strip(),
                str(entry["sj_div"]).strip(),
                (str(entry["account_id"]).strip() if entry.get("account_id") else None),
                str(entry["account_nm"]).strip(),
                entry.get("amount_krw"),
                entry.get("amount_raw"),
                str(entry.get("currency") or "KRW").strip(),
                str(entry.get("period_label") or f"{int(entry['bsns_year'])}{entry['reprt_code']}").strip(),
                entry.get("fiscal_period"),
            )
            for entry in entries
            if all(entry.get(key) not in (None, "") for key in _REQUIRED_KEYS)
        ]
        if not rows:
            return 0

        await self._connection.executemany(
            f"""
            INSERT INTO dart_financial_facts ({_INSERT_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            ON CONFLICT {_CONFLICT_TARGET}
            {_DO_UPDATE}
            """,
            rows,
        )
        return len(rows)

    async def list_by_corp_year(self, corp_code: str, bsns_year: int) -> Any:
        return await self._connection.fetch(
            """
            SELECT *
            FROM dart_financial_facts
            WHERE corp_code = $1 AND bsns_year = $2
            ORDER BY sj_div, account_id, id
            """,
            corp_code.strip(),
            int(bsns_year),
        )

    async def get_latest_rcept_no(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
        fs_div: str,
    ) -> Any:
        return await self._connection.fetchval(
            """
            SELECT MAX(rcept_no)
            FROM dart_financial_facts
            WHERE corp_code = $1 AND bsns_year = $2 AND reprt_code = $3 AND fs_div = $4
            """,
            corp_code.strip(),
            int(bsns_year),
            reprt_code.strip(),
            fs_div.strip(),
        )
