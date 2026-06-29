"""Google Patents (BigQuery) patent source — fetch + map to KiprisPatentRecord.

Importable seam shared by the manual backfill CLI
(``scripts/backfill_patents_bigquery.py``) and any future automated driver, so
the BigQuery query/attribution logic lives in one place and persists through the
*same* DB contract as KIPRIS via ``PatentCollector.ingest_records``.

KIPRIS lags differently from BigQuery (BigQuery trails ~18 months on the newest
filings), so the two are complementary in time: KIPRIS=latest, BigQuery=history.
Where they overlap, ``canonicalize_application_no`` makes the same patent collapse
on the ``source_hash`` UNIQUE constraint.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.collectors.patent.application_no import canonicalize_application_no

BQ_TABLE = "patents-public-data.patents.publications"
DEFAULT_BQ_PROJECT = "patent-bq-reader"
SOURCE_NAME = "GOOGLE_PATENTS"

# Ticker -> BigQuery assignee_harmonized.name UPPER LIKE patterns. Mirrors
# DEFAULT_COMPANIES.bq_like in scripts/patent_source_audit.py. ASCII-only so the
# same patterns are safe in the BigQuery console too.
TICKER_BQ_PATTERNS: dict[str, list[str]] = {
    "005930": ["%SAMSUNG ELECTRONICS%"],  # 삼성전자 (Samsung Electronics only — not SDI/Display/SDS)
    "000660": ["%SK HYNIX%"],             # SK하이닉스
    "035420": ["%NAVER%"],                # NAVER
}


def like_predicate(pattern: str) -> Callable[[str], bool]:
    """Translate a SQL ``LIKE`` pattern (our patterns use only ``%`` wildcards on
    the ends) into a Python predicate over an already-upper-cased string."""
    p = pattern.upper()
    core = p.strip("%")
    starts, ends = p.startswith("%"), p.endswith("%")
    if starts and ends:
        return lambda s: core in s
    if starts:
        return lambda s: s.endswith(core)
    if ends:
        return lambda s: s.startswith(core)
    return lambda s: s == core


def fmt_yyyymmdd(value: Any) -> str | None:
    """BigQuery dates are INT64 ``YYYYMMDD``. Return an 8-char string with any
    ``00`` month/day clamped to ``01`` (some records carry a zero day), or None."""
    if not value:
        return None
    s = str(int(value))
    if len(s) != 8:
        return None
    y, m, d = s[:4], s[4:6], s[6:8]
    if m == "00":
        m = "01"
    if d == "00":
        d = "01"
    return f"{y}{m}{d}"


def bq_rows(*, start_year: int, end_year: int, patterns: list[str], project: str) -> list[dict]:
    """Fetch one row per (matching) publication across all target patterns in a
    single scan. Attribution to a specific stock happens later in Python."""
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        raise SystemExit(
            "google-cloud-bigquery is not installed — run via "
            "`uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py ...`"
        )
    try:
        client = bigquery.Client(project=project)
    except Exception as exc:  # DefaultCredentialsError 등
        raise SystemExit(
            f"BigQuery client init failed ({exc}).\n"
            "Run `gcloud auth application-default login` (project patent-bq-reader) first."
        )

    sql = f"""
    SELECT
      application_number,
      filing_date,
      publication_number,
      publication_date,
      (SELECT t.text FROM UNNEST(title_localized) t
         ORDER BY CASE LOWER(t.language) WHEN 'ko' THEN 0 WHEN 'en' THEN 1 ELSE 2 END
         LIMIT 1) AS title,
      (SELECT i.code FROM UNNEST(ipc) i LIMIT 1) AS ipc_code,
      ARRAY(SELECT a.name FROM UNNEST(assignee_harmonized) a) AS assignees
    FROM `{BQ_TABLE}`
    WHERE country_code = 'KR'
      AND filing_date BETWEEN @start AND @end
      AND EXISTS (
        SELECT 1 FROM UNNEST(assignee_harmonized) a, UNNEST(@patterns) p
        WHERE UPPER(a.name) LIKE p
      )
    """.strip()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("patterns", "STRING", [p.upper() for p in patterns]),
            bigquery.ScalarQueryParameter("start", "INT64", start_year * 10000 + 101),
            bigquery.ScalarQueryParameter("end", "INT64", end_year * 10000 + 1231),
        ]
    )
    rows = client.query(sql, job_config=job_config).result()
    return [
        {
            "application_number": str(r["application_number"]),
            "filing_date": r["filing_date"],
            "publication_number": r["publication_number"],
            "publication_date": r["publication_date"],
            "title": r["title"],
            "ipc_code": r["ipc_code"],
            "assignees": list(r["assignees"] or []),
        }
        for r in rows
    ]


def build_records(rows: list[dict], ticker: str) -> list[Any]:
    """Attribute rows to ``ticker`` (assignee matches its patterns), de-dup by the
    canonical application_no key, and build KiprisPatentRecord objects for
    ``ingest_records``."""
    from app.clients.kipris_client import KiprisPatentRecord

    preds = [like_predicate(p) for p in TICKER_BQ_PATTERNS[ticker]]
    seen: set[str] = set()
    records: list[Any] = []
    for row in rows:
        assignees = row["assignees"]
        matched = next((a for a in assignees if any(pred(a.upper()) for pred in preds)), None)
        if matched is None:
            continue
        app_no = row["application_number"]
        key = canonicalize_application_no(app_no)
        if not key or key in seen:
            continue
        seen.add(key)
        records.append(
            KiprisPatentRecord(
                application_no=app_no,
                invention_title=(row["title"] or app_no),
                applicant_name=matched,
                application_date=fmt_yyyymmdd(row["filing_date"]),
                ipc_code=row["ipc_code"],
                open_date=fmt_yyyymmdd(row["publication_date"]),
                registration_number=None,
                abstract=None,
                raw={
                    "source": "google_patents_bigquery",
                    "bq_table": BQ_TABLE,
                    "publication_number": row["publication_number"],
                    "publication_date": fmt_yyyymmdd(row["publication_date"]),
                    "filing_date": fmt_yyyymmdd(row["filing_date"]),
                    "assignees": assignees,
                    "ipc_code": row["ipc_code"],
                    "title": row["title"],
                    "matched_ticker": ticker,
                },
            )
        )
    return records
