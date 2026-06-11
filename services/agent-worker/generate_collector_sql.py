"""
Standalone collector SQL generator.

Calls KIPRIS + Naver DataLab APIs, collects all records, and writes a single
self-contained DO $$ ... $$ block per section. Apply those files via Supabase
MCP execute_sql (or any psql client).

Collection targets are loaded dynamically from the database (DATABASE_URL):

- Patent targets:   SELECT id, ticker, name FROM stocks WHERE is_active = TRUE
- DataLab targets:  active datalab_categories JOINed with datalab_category_stocks

Adding a stock to `stocks` (and wiring it into `datalab_category_stocks`)
automatically includes it in the next run — no code change required.

`category_registry.json` is now only a reviewed keyword fallback. The DB is the
source of truth for which categories/stocks are active.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

import asyncpg  # type: ignore[import]

from app.collectors.patent.applicant_aliases import build_applicant_aliases

# ── Load .env ────────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent.parent.parent / ".env"
for _line in _env_path.read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

KIPRIS_KEY     = os.environ["KIPRIS_API_KEY"]
NAVER_ID       = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET   = os.environ["NAVER_CLIENT_SECRET"]
COLLECTOR_VER  = os.environ.get("COLLECTOR_VERSION", "1.0")
DATABASE_URL   = os.environ.get("DATABASE_URL", "")

REGISTRY_PATH = Path(__file__).parent / "category_registry.json"
OUTPUT_PATH = Path(__file__).parent / "collector_inserts.sql"


def _yesterday() -> date:
    return date.today() - timedelta(days=1)


def _datalab_date(value: str | None) -> str:
    return (value or str(_yesterday())).replace("/", "-")


def _kipris_date(value: str | None) -> str:
    return _datalab_date(value).replace("-", "")


START_DATE = _datalab_date(os.environ.get("COLLECTOR_START_DATE"))
END_DATE = _datalab_date(os.environ.get("COLLECTOR_END_DATE"))
KIPRIS_START = _kipris_date(os.environ.get("COLLECTOR_START_DATE"))
KIPRIS_END = _kipris_date(os.environ.get("COLLECTOR_END_DATE"))


# ── Target loading (DB primary, files as fallback / keyword dictionary) ────────

def _parse_dsn(dsn: str) -> dict:
    """Parse a postgres DSN into asyncpg.connect kwargs.

    Done manually because urllib's parser chokes on special characters in the
    password (Supabase passwords frequently contain them).
    """
    m = re.match(
        r"^postgres(?:ql)?://(?P<user>[^:]+):(?P<password>.*)@"
        r"(?P<host>[^:/@]+):(?P<port>\d+)/(?P<db>[^?]+)",
        dsn,
    )
    if not m:
        raise ValueError("Could not parse DATABASE_URL")
    return {
        "user": unquote(m.group("user")),
        "password": unquote(m.group("password")),
        "host": m.group("host"),
        "port": int(m.group("port")),
        "database": m.group("db"),
    }


async def _fetch_db_targets(dsn: str) -> tuple[list[dict], list[dict]]:
    """Return (stock_rows, datalab_category_stock_rows) from the DB."""
    conn = await asyncpg.connect(**_parse_dsn(dsn))
    try:
        stock_rows = await conn.fetch(
            "SELECT id, ticker, name FROM stocks WHERE is_active = TRUE ORDER BY id"
        )
        has_keyword_table = await conn.fetchval(
            "SELECT to_regclass('public.datalab_category_keywords') IS NOT NULL"
        )
        if has_keyword_table:
            datalab_rows = await conn.fetch(
                """
                SELECT
                    dc.id,
                    dc.name,
                    dcs.stock_id,
                    dcs.weight,
                    dck.keyword,
                    dck.keyword_group
                FROM datalab_categories dc
                JOIN datalab_category_stocks dcs ON dcs.category_id = dc.id
                JOIN datalab_category_keywords dck ON dck.category_id = dc.id AND dck.is_active = TRUE
                WHERE dc.is_active = TRUE
                ORDER BY dc.id, dcs.stock_id, dck.keyword
                """
            )
        else:
            datalab_rows = await conn.fetch(
                """
                SELECT dc.id, dc.name, dcs.stock_id, dcs.weight
                FROM datalab_categories dc
                JOIN datalab_category_stocks dcs ON dcs.category_id = dc.id
                WHERE dc.is_active = TRUE
                ORDER BY dc.id, dcs.stock_id
                """
            )
    finally:
        await conn.close()
    return [dict(r) for r in stock_rows], [dict(r) for r in datalab_rows]


def load_db_targets() -> tuple[list[dict], list[dict]]:
    """Load patent + datalab raw rows from the DB. Returns ([], []) on failure."""
    if not DATABASE_URL:
        print("  WARN: DATABASE_URL not set — falling back to committed config.", file=sys.stderr)
        return [], []
    try:
        return asyncio.run(_fetch_db_targets(DATABASE_URL))
    except Exception as exc:  # noqa: BLE001 — any DB error → fallback
        print(f"  WARN: DB target load failed ({exc}) — falling back to committed config.", file=sys.stderr)
        return [], []


def build_patent_targets(stock_rows: list[dict]) -> list[dict]:
    """DB stocks → patent targets."""
    if stock_rows:
        return [
            {"stock_id": r["id"], "ticker": r["ticker"], "stock_name": r["name"]}
            for r in stock_rows
        ]
    return []


def build_datalab_categories(datalab_rows: list[dict]) -> list[dict]:
    """Merge DB-active categories with keywords from category_registry.json.

    The DB decides which categories/stocks are active; the registry supplies the
    keyword dictionary (matched by category_id). Categories that are active in
    the DB but have no keywords in the registry are skipped with a warning.
    If the DB is unreachable, fall back to the registry's own category list.
    """
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    keywords_by_id = {c["category_id"]: c for c in registry}

    if not datalab_rows:
        print("  Using category_registry.json as DataLab fallback (no DB rows).", file=sys.stderr)
        return [
            {
                "category_id": c["category_id"],
                "category_name": c["category_name"],
                "keyword_group": c["keyword_group"],
                "keywords": c["keywords"],
            }
            for c in registry
        ]

    grouped: dict[int, dict] = {}
    for row in datalab_rows:
        cid = row["id"]
        cat = grouped.setdefault(
            cid,
            {"category_id": cid, "category_name": row["name"], "linked_stocks": []},
        )
        linked_stock = {"stock_id": row["stock_id"], "weight": float(row["weight"])}
        if linked_stock not in cat["linked_stocks"]:
            cat["linked_stocks"].append(linked_stock)
        if row.get("keyword"):
            cat.setdefault("keyword_group", row["keyword_group"] or row["name"])
            cat.setdefault("keywords", [])
            if row["keyword"] not in cat["keywords"]:
                cat["keywords"].append(row["keyword"])

    categories: list[dict] = []
    for cid, cat in sorted(grouped.items()):
        if cat.get("keywords"):
            categories.append(cat)
            continue
        kw = keywords_by_id.get(cid)
        if not kw:
            print(
                f"  WARN: category {cid} ({cat['category_name']}) is active in DB "
                f"but has no keywords in category_registry.json — skipping.",
                file=sys.stderr,
            )
            continue
        cat["keyword_group"] = kw["keyword_group"]
        cat["keywords"] = kw["keywords"]
        categories.append(cat)
    return categories

# ── IPC category maps (from patent collector) ─────────────────────────────────
IPC_SUBCLASS_MAP = {
    "G06": "AI/Software", "G16": "Information Technology",
    "H04": "Communications", "H01L": "Semiconductors",
    "H02": "Power Engineering", "A61": "Pharmaceuticals",
    "C12": "Biotechnology", "B60": "Automotive",
    "G01": "Measurement/Sensors",
}
IPC_SECTION_MAP = {
    "A": "Life Sciences", "B": "Industrial Processes", "C": "Chemistry",
    "D": "Textiles", "E": "Construction", "F": "Mechanical Engineering",
    "G": "Physics/IT", "H": "Electronics/Semiconductors",
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def make_source_hash(*parts) -> str:
    normalized = ["" if p is None else str(p).strip().lower() for p in parts]
    return hashlib.sha256("|".join(normalized).encode()).hexdigest()


def sql_lit(value) -> str:
    """Escape a Python value to a safe SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # String: escape single quotes and wrap
    return "'" + str(value).replace("'", "''") + "'"


def parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return datetime.strptime(v, "%Y%m%d").date()
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return date.today()


def tech_category(ipc_code: str | None) -> str | None:
    if not ipc_code:
        return None
    code = ipc_code.strip().upper()
    for prefix, cat in IPC_SUBCLASS_MAP.items():
        if code.startswith(prefix):
            return cat
    return IPC_SECTION_MAP.get(code[0]) if code else None


def compute_change_pct(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    if previous == 0:
        return 999.99 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 2)


# ── KIPRIS API ─────────────────────────────────────────────────────────────────

KIPRIS_BASE = "https://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice"


def kipris_fetch_page(applicant: str, page_no: int) -> tuple[list[dict], int]:
    params = urlencode({
        "accessKey": KIPRIS_KEY,
        "applicant": applicant,
        "startPage": str(page_no),
        "numOfRows": "100",
        "sortSpec": "AD",
        "descSort": "true",
    })
    url = f"{KIPRIS_BASE}/applicantNameSearchInfo?{params}"
    req = Request(url, headers={"Accept": "application/xml"})
    with urlopen(req, timeout=30) as resp:
        xml_text = resp.read().decode("utf-8")
    root = ET.fromstring(xml_text)
    # KIPRIS Plus API uses capitalized tag names and TotalSearchCount
    total_raw = (
        root.findtext(".//TotalSearchCount")
        or root.findtext(".//totalCount")
        or "0"
    )
    total = int(total_raw.strip())
    records = []
    for item in (
        root.findall(".//PatentUtilityInfo")
        or root.findall(".//item")
        or root.findall(".//patUtiModInfoSearchSeviceInfoBrief")
    ):
        raw = {child.tag: (child.text or "") for child in item}
        app_no = (
            item.findtext("ApplicationNumber")
            or item.findtext("applicationNumber")
            or item.findtext("applno")
            or ""
        ).strip()
        if not app_no:
            continue
        title = (
            item.findtext("InventionName")
            or item.findtext("inventionTitle")
            or item.findtext("inventionName")
            or ""
        ).strip()
        ipc = (
            item.findtext("InternationalpatentclassificationNumber")
            or item.findtext("ipcCode")
            or item.findtext("ipc")
        )
        records.append({
            "application_no": app_no,
            "invention_title": title,
            "applicant_name": (
                item.findtext("Applicant") or item.findtext("applicantName")
            ),
            "application_date": (
                item.findtext("ApplicationDate")
                or item.findtext("applicationDate")
                or item.findtext("appDate")
            ),
            "ipc_code": ipc,
            "raw": raw,
        })
    return records, total


def collect_patents(applicant: str, max_pages: int = 1) -> list[dict]:
    """Collect patents from KIPRIS. Limited to max_pages (100 records each)."""
    all_records = []
    for page in range(1, max_pages + 1):
        print(f"  KIPRIS page {page}/{max_pages} …", flush=True)
        records, total = kipris_fetch_page(applicant, page)
        all_records.extend(records)
        if not records or len(all_records) >= total:
            break
    print(f"  total available in KIPRIS: {total:,}", flush=True)
    return all_records


def collect_patents_for_aliases(applicants: list[str], max_pages: int = 1) -> list[dict]:
    """Collect KIPRIS patents for multiple applicant aliases, deduped by application number."""
    records_by_application_no: dict[str, dict] = {}
    for applicant in applicants:
        print(f"  applicant alias: {applicant}", flush=True)
        for record in collect_patents(applicant, max_pages=max_pages):
            records_by_application_no.setdefault(record["application_no"], record)
    return list(records_by_application_no.values())


# ── Naver DataLab API ──────────────────────────────────────────────────────────

NAVER_URL = "https://openapi.naver.com/v1/datalab/search"


def naver_search_keyword(keyword_group: str, keyword: str) -> list[dict]:
    """One API call for ONE keyword — returns that keyword's own series.

    Each keyword gets its own keywordGroup so Naver does NOT aggregate it with
    siblings; the stored `keyword` label is exactly the term that was measured.
    """
    body = json.dumps({
        "startDate": START_DATE,
        "endDate": END_DATE,
        "timeUnit": "date",
        "keywordGroups": [{"groupName": keyword, "keywords": [keyword]}],
        "device": "",
        "gender": "",
        "ages": [],
    }, ensure_ascii=False).encode("utf-8")
    req = Request(
        NAVER_URL,
        data=body,
        headers={
            "X-Naver-Client-Id": NAVER_ID,
            "X-Naver-Client-Secret": NAVER_SECRET,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    records = []
    for result in data.get("results", []):
        for point in result.get("data", []):
            records.append({
                "keyword": keyword,
                "keyword_group": keyword_group,
                "period": point["period"],
                "ratio": float(point.get("ratio", 0.0)),
                # per-point payload only — embedding the full series in every
                # row bloats generated SQL past MCP execute_sql size limits
                "raw_data": {"title": result.get("title"), "point": point},
            })
    return records


# ── SQL statement builders ────────────────────────────────────────────────────

def build_patent_block(target: dict, records: list[dict]) -> list[str]:
    """Return list of SQL statement strings for one stock's patents."""
    stock_id = target["stock_id"]
    ticker = target["ticker"]
    statements = []

    # collector_run INSERT → run_id via lastval()
    statements.append(f"""
    -- Patent run: {ticker}
    INSERT INTO collector_runs (collector_type, run_mode, status)
    VALUES ('PATENT', 'batch', 'running');
    v_patent_run_{ticker} := lastval();""")

    seen_hashes = set()
    known_cats: set[str] = set()
    inserted = 0
    skipped = 0

    for rec in records:
        app_no = rec["application_no"]
        src_hash = make_source_hash("PATENT", app_no)
        if src_hash in seen_hashes:
            skipped += 1
            continue
        seen_hashes.add(src_hash)

        app_date = parse_date(rec["application_date"])
        tech_cat = tech_category(rec["ipc_code"])
        is_new = (tech_cat is not None) and (tech_cat not in known_cats)
        if tech_cat:
            known_cats.add(tech_cat)

        title = rec["invention_title"] or app_no
        extra = json.dumps(rec["raw"], ensure_ascii=False)

        statements.append(f"""
    -- Patent {app_no}
    INSERT INTO raw_documents (
        stock_id, collector_run_id, source_type, source_name,
        external_id, source_hash, title, published_at, collect_status, collector_ver
    )
    VALUES (
        {stock_id}, v_patent_run_{ticker}, 'PATENT', 'KIPRIS',
        {sql_lit(app_no)}, {sql_lit(src_hash)}, {sql_lit(title)},
        {sql_lit(str(app_date))}, 'success', {sql_lit(COLLECTOR_VER)}
    )
    ON CONFLICT DO NOTHING;
    IF FOUND THEN
        v_raw_id := lastval();
        INSERT INTO patent_raw_details (
            raw_document_id, stock_id, application_no, patent_title,
            applicant_name, application_date, tech_category, is_new_category, extra_payload
        )
        VALUES (
            v_raw_id, {stock_id}, {sql_lit(app_no)}, {sql_lit(title)},
            {sql_lit(rec["applicant_name"])}, {sql_lit(str(app_date))},
            {sql_lit(tech_cat)}, {is_new}, {sql_lit(extra)}
        );
        INSERT INTO processing_queue (stock_id, task_type, status, priority, source_raw_ids, task_context)
        VALUES (
            {stock_id}, 'NORMALIZE_PATENT', 'pending', 'batch',
            ARRAY[v_raw_id],
            jsonb_build_object(
                'collector_run_id', v_patent_run_{ticker},
                'source_type', 'PATENT',
                'collector_ver', {sql_lit(COLLECTOR_VER)}
            )
        );
        v_patent_inserted_{ticker} := v_patent_inserted_{ticker} + 1;
    ELSE
        v_patent_skipped_{ticker} := v_patent_skipped_{ticker} + 1;
    END IF;""")
        inserted += 1

    statements.append(f"""
    UPDATE collector_runs
    SET status = CASE WHEN v_patent_inserted_{ticker} > 0 OR v_patent_skipped_{ticker} > 0 THEN 'success' ELSE 'failed' END,
        finished_at = NOW(),
        collected_count = {len(records)},
        inserted_count = v_patent_inserted_{ticker},
        skipped_count = v_patent_skipped_{ticker},
        failed_count = 0
    WHERE id = v_patent_run_{ticker};""")

    return statements, inserted


def build_datalab_keyword_sql(cat: dict, keyword: str, records: list[dict]) -> tuple[str, int]:
    """Build one self-contained compact DO block for one (category, keyword) series.

    Compact bulk-VALUES format (one collector_run per keyword series) keeps each
    file small enough for a single MCP execute_sql call. Returns (sql, n_rows).
    """
    cat_id = cat["category_id"]
    cat_name = cat["category_name"]
    kw_group = cat["keyword_group"]

    doc_values: list[str] = []
    det_values: list[str] = []
    seen_hashes: set[str] = set()
    prev: float | None = None

    for rec in records:
        observed = rec["period"]
        ratio = rec["ratio"]

        src_hash = make_source_hash("DATALAB", cat_id, keyword, observed, "daily", "all", "all", "all")
        if src_hash in seen_hashes:
            continue
        seen_hashes.add(src_hash)

        change_pct = compute_change_pct(ratio, prev)
        is_spike = ratio >= 80.0 or (change_pct is not None and change_pct >= 50.0)
        external_id = f"{cat_id}_{keyword}_{observed}_daily_all_all_all"
        title = f"[DataLab] {cat_name} / {keyword} {observed}"
        payload = json.dumps(rec["raw_data"], ensure_ascii=False)

        doc_values.append(
            f"    ({sql_lit(src_hash)},{sql_lit(external_id)},{sql_lit(title)},{sql_lit(observed)})"
        )
        det_values.append(
            f"    ({sql_lit(external_id)},{sql_lit(observed)},{ratio},{sql_lit(prev)},"
            f"{sql_lit(change_pct)},{sql_lit(is_spike)},{sql_lit(payload)})"
        )
        prev = ratio

    n_rows = len(doc_values)
    if n_rows == 0:
        return "", 0

    doc_values_block = ",\n".join(doc_values)
    det_values_block = ",\n".join(det_values)
    sql = f"""-- Signal Alpha DataLab Inserts: {cat_name} / {keyword}
-- Generated: {datetime.now().isoformat()}
-- Window: {START_DATE} ~ {END_DATE}

DO $DL$
DECLARE v_run BIGINT;
BEGIN
INSERT INTO collector_runs (collector_type,run_mode,status) VALUES ('DATALAB','batch','running');
v_run := lastval();

INSERT INTO datalab_raw_documents (category_id,collector_run_id,source_name,external_id,source_hash,title,published_at,collect_status,collector_ver)
SELECT {cat_id}, v_run, 'NAVER_DATALAB', v.ext_id, v.src_hash, v.title, v.obs::DATE, 'success', {sql_lit(COLLECTOR_VER)}
FROM (VALUES
{doc_values_block}
) AS v(src_hash, ext_id, title, obs)
ON CONFLICT DO NOTHING;

INSERT INTO datalab_raw_details (raw_document_id,category_id,keyword,keyword_group,observed_date,search_index,previous_search_index,change_pct,period_type,device,gender,age_group,is_spike,extra_payload)
SELECT d.id, {cat_id}, {sql_lit(keyword)}, {sql_lit(kw_group)}, v.obs::DATE,
       v.ratio::numeric, v.prev::numeric, v.cpct::numeric,
       'daily', 'all', 'all', 'all', v.is_spike::boolean, v.payload::jsonb
FROM datalab_raw_documents d
JOIN (VALUES
{det_values_block}
) AS v(ext_id, obs, ratio, prev, cpct, is_spike, payload)
  ON d.external_id = v.ext_id AND d.category_id = {cat_id}
WHERE d.collector_run_id = v_run
ON CONFLICT DO NOTHING;

INSERT INTO processing_queue (stock_id,task_type,status,priority,source_raw_ids,task_context)
SELECT NULL, 'NORMALIZE_DATALAB', 'pending', 'batch', ARRAY[d.id],
    jsonb_build_object('collector_run_id',v_run,'source_type','DATALAB','category_id',{cat_id},'keyword',{sql_lit(keyword)},'period_type','daily','collector_ver',{sql_lit(COLLECTOR_VER)})
FROM datalab_raw_documents d
WHERE d.category_id = {cat_id} AND d.collector_run_id = v_run;

UPDATE collector_runs SET status='success',finished_at=NOW(),collected_count={n_rows},
    inserted_count=(SELECT COUNT(*) FROM datalab_raw_documents WHERE category_id={cat_id} AND collector_run_id=v_run),
    skipped_count={n_rows}-(SELECT COUNT(*) FROM datalab_raw_documents WHERE category_id={cat_id} AND collector_run_id=v_run),
    failed_count=0
WHERE id=v_run;
END; $DL$;
"""
    return sql, n_rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Signal Alpha Collector SQL Generator ===\n")
    datalab_only = "--datalab-only" in sys.argv
    patent_only = "--patent-only" in sys.argv

    # ── Load targets dynamically from the DB (fallback to committed config) ───
    stock_rows, datalab_rows = load_db_targets()
    patent_targets = [] if datalab_only else build_patent_targets(stock_rows)
    datalab_categories = [] if patent_only else build_datalab_categories(datalab_rows)
    print(
        f"Targets — patents: {len(patent_targets)} stocks | "
        f"DataLab: {len(datalab_categories)} categories\n"
    )

    # sections = [(name, stmts, decls), ...]
    sections = []
    written = []
    summary = {"patent": [], "datalab": []}

    # ── Patent collection ─────────────────────────────────────────────────────
    print("PATENT COLLECTOR")
    print("=" * 50)
    for target in patent_targets:
        ticker = target["ticker"]
        applicant_names = build_applicant_aliases(
            stock_name=target.get("stock_name"),
            applicant_name=target.get("applicant_name"),
            applicant_names=target.get("applicant_names"),
        )
        if not applicant_names:
            raise ValueError(f"Patent target {ticker} needs stock_name or applicant_names.")
        print(f"\n>> {ticker} ({', '.join(applicant_names)}) ...")
        section_decls = [
            f"    v_patent_run_{ticker} BIGINT;",
            f"    v_patent_inserted_{ticker} INT := 0;",
            f"    v_patent_skipped_{ticker} INT := 0;",
        ]
        try:
            records = collect_patents_for_aliases(applicant_names, max_pages=1)
            print(f"  -> {len(records)} records from KIPRIS")
            stmts, n_insert = build_patent_block(target, records)
            sections.append((f"patent_{ticker}", stmts, section_decls))
            summary["patent"].append({
                "ticker": ticker,
                "collected": len(records),
                "to_insert": n_insert,
            })
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            summary["patent"].append({"ticker": ticker, "error": str(exc)})

    # ── DataLab collection — one API call + one SQL file per keyword ─────────
    print("\nDATALAB COLLECTOR (per-keyword)")
    print("=" * 50)
    for cat in datalab_categories:
        name = cat["category_name"]
        print(f"\n>> {name} ({len(cat['keywords'])} keywords) ...")
        for kw_idx, keyword in enumerate(cat["keywords"], 1):
            try:
                records = naver_search_keyword(cat["keyword_group"], keyword)
                sql, n_rows = build_datalab_keyword_sql(cat, keyword, records)
                print(f"  [{kw_idx}] {keyword}: {len(records)} data points")
                if not sql:
                    summary["datalab"].append({
                        "category": f"{name}/{keyword}", "collected": 0, "to_insert": 0,
                    })
                    continue
                fname = OUTPUT_PATH.parent / f"collector_datalab_{name}_kw{kw_idx}.sql"
                fname.write_text(sql, encoding="utf-8")
                written.append((f"datalab_{name}_kw{kw_idx}", fname, fname.stat().st_size))
                summary["datalab"].append({
                    "category": f"{name}/{keyword}",
                    "collected": len(records),
                    "to_insert": n_rows,
                })
                time.sleep(0.15)  # stay well under Naver's per-second rate limit
            except Exception as exc:
                print(f"  ERROR [{keyword}]: {exc}", file=sys.stderr)
                summary["datalab"].append({"category": f"{name}/{keyword}", "error": str(exc)})

    # ── Write patent SQL files (split by section for MCP size limits) ────────
    for section_name, section_stmts, section_decls in sections:
        if not section_stmts:
            continue
        decl_block = "\n".join(section_decls)
        body_block = "\n".join(section_stmts)
        sql = f"""-- Signal Alpha Collector Inserts: {section_name}
-- Generated: {datetime.now().isoformat()}
-- Window: {START_DATE} ~ {END_DATE}

DO $COLLECTOR$
DECLARE
    v_raw_id BIGINT;
{decl_block}
BEGIN
{body_block}
END;
$COLLECTOR$;
"""
        fname = OUTPUT_PATH.parent / f"collector_{section_name}.sql"
        fname.write_text(sql, encoding="utf-8")
        written.append((section_name, fname, fname.stat().st_size))

    for section_name, fname, sz in written:
        print(f"  {section_name}: {fname.name} ({sz:,} bytes)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n=== COLLECTION SUMMARY ===")
    print(f"{'ticker':<10} {'collected':>9} {'to_insert':>9}")
    print("-" * 30)
    for r in summary["patent"]:
        if "error" in r:
            print(f"{r['ticker']:<10} ERROR: {r['error']}")
        else:
            print(f"{r['ticker']:<10} {r['collected']:>9} {r['to_insert']:>9}")

    print(f"\n{'category/keyword':<34} {'collected':>9} {'to_insert':>9}")
    print("-" * 55)
    for r in summary["datalab"]:
        if "error" in r:
            print(f"{r['category']:<34} ERROR: {r['error']}")
        else:
            print(f"{r['category']:<34} {r['collected']:>9} {r['to_insert']:>9}")

    print("\nNext: apply the written collector_*.sql files via Supabase MCP execute_sql.")


if __name__ == "__main__":
    main()
