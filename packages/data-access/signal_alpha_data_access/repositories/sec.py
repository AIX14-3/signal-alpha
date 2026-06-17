from __future__ import annotations

from datetime import date, datetime
from typing import Any


class SecFilingRepository:
    """SEC EDGAR 공시 목록(sec_filings) 리포지토리.

    accession_no를 자연키로 멱등 upsert 한다. (DART의 DartRepository와 동일 패턴)
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def upsert_filing(
        self,
        *,
        cik: str,
        ticker: str,
        form: str,
        filing_date: Any,
        accession_no: str,
        company_name: str | None = None,
        primary_document: str | None = None,
        primary_doc_description: str | None = None,
        document_url: str | None = None,
        stock_id: int | None = None,
    ) -> Any:
        return await self._connection.fetchrow(
            """
            INSERT INTO sec_filings (
                stock_id,
                cik,
                ticker,
                company_name,
                form,
                filing_date,
                accession_no,
                primary_document,
                primary_doc_description,
                document_url
            )
            VALUES ($1, $2, $3, $4, $5, $6::DATE, $7, $8, $9, $10)
            ON CONFLICT (accession_no)
            DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                cik = EXCLUDED.cik,
                ticker = EXCLUDED.ticker,
                company_name = EXCLUDED.company_name,
                form = EXCLUDED.form,
                filing_date = EXCLUDED.filing_date,
                primary_document = EXCLUDED.primary_document,
                primary_doc_description = EXCLUDED.primary_doc_description,
                document_url = EXCLUDED.document_url,
                updated_at = NOW()
            RETURNING *
            """,
            stock_id,
            _normalize_cik(cik),
            ticker.strip(),
            company_name,
            form.strip(),
            _to_date(filing_date),
            accession_no.strip(),
            primary_document,
            primary_doc_description,
            document_url,
        )

    async def upsert_filings(self, entries: list[dict[str, Any]]) -> int:
        """공시 목록 일괄 멱등 적재. accession_no/cik/ticker/form/filing_date 필수."""
        rows = [
            (
                entry.get("stock_id"),
                _normalize_cik(entry["cik"]),
                str(entry["ticker"]).strip(),
                entry.get("company_name"),
                str(entry["form"]).strip(),
                _to_date(entry["filing_date"]),
                str(entry["accession_no"]).strip(),
                entry.get("primary_document"),
                entry.get("primary_doc_description"),
                entry.get("document_url"),
            )
            for entry in entries
            if entry.get("accession_no")
            and entry.get("cik")
            and entry.get("ticker")
            and entry.get("form")
            and entry.get("filing_date")
        ]
        if not rows:
            return 0

        await self._connection.executemany(
            """
            INSERT INTO sec_filings (
                stock_id,
                cik,
                ticker,
                company_name,
                form,
                filing_date,
                accession_no,
                primary_document,
                primary_doc_description,
                document_url
            )
            VALUES ($1, $2, $3, $4, $5, $6::DATE, $7, $8, $9, $10)
            ON CONFLICT (accession_no)
            DO UPDATE SET
                stock_id = EXCLUDED.stock_id,
                cik = EXCLUDED.cik,
                ticker = EXCLUDED.ticker,
                company_name = EXCLUDED.company_name,
                form = EXCLUDED.form,
                filing_date = EXCLUDED.filing_date,
                primary_document = EXCLUDED.primary_document,
                primary_doc_description = EXCLUDED.primary_doc_description,
                document_url = EXCLUDED.document_url,
                updated_at = NOW()
            """,
            rows,
        )
        return len(rows)

    async def get_by_accession(self, accession_no: str) -> Any:
        return await self._connection.fetchrow(
            """
            SELECT *
            FROM sec_filings
            WHERE accession_no = $1
            """,
            accession_no.strip(),
        )

    async def list_by_ticker(self, ticker: str, *, limit: int = 20) -> Any:
        return await self._connection.fetch(
            """
            SELECT *
            FROM sec_filings
            WHERE ticker = $1
            ORDER BY filing_date DESC, id DESC
            LIMIT $2
            """,
            ticker.strip(),
            limit,
        )

    async def get_latest_filing_date(self, cik: str) -> Any:
        return await self._connection.fetchval(
            """
            SELECT MAX(filing_date)
            FROM sec_filings
            WHERE cik = $1
            """,
            _normalize_cik(cik),
        )


def _normalize_cik(value: Any) -> str:
    """CIK를 zero-padded 10자리 문자열로 정규화 (예: 1045810 → '0001045810')."""
    text = str(value).strip()
    if text.isdigit():
        return f"{int(text):010d}"
    return text


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()
