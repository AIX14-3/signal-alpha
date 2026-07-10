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

# Ticker -> BigQuery assignee_harmonized.name UPPER LIKE patterns. ASCII-only so
# the same patterns are safe in the BigQuery console too. prod 적재분의 실제
# 출원인 이름에서 도출(2026-06-30 실측). 그룹 내 종목은 서로 겹치지 않는 부분문자열로
# 좁혀 계열사 오염을 막는다(삼성전자 vs 삼성SDI, LG 4사, 현대 3사, 한미약품 vs 한미반도체).
# 사명 변경/복수 변형은 리스트로 여러 패턴(예: LX세미콘=구 실리콘웍스).
TICKER_BQ_PATTERNS: dict[str, list[str]] = {
    # 전자/반도체/IT
    "005930": ["%SAMSUNG ELECTRONICS%"],   # 삼성전자 (SDI/Display/SDS 제외)
    "006400": ["%SAMSUNG SDI%"],           # 삼성SDI
    "000660": ["%SK HYNIX%"],              # SK하이닉스
    "066570": ["%LG ELECTRONICS%"],        # LG전자
    "034220": ["%LG DISPLAY%"],            # LG디스플레이
    "011070": ["%LG INNOTEK%"],            # LG이노텍
    "000990": ["%DB HITEK%"],              # DB하이텍
    "240810": ["%WONIK IPS%"],             # 원익IPS
    "042700": ["%HANMI SEMICONDUCTOR%"],   # 한미반도체 (한미약품과 구분)
    "054450": ["%TELECHIPS%"],             # 텔레칩스
    "108320": ["%LX SEMICON%", "%SILICON WORKS%"],  # LX세미콘 (구 실리콘웍스)
    # 화학/2차전지/소재
    "051910": ["%LG CHEM%"],               # LG화학 (LG CHEMICAL/LG CHEM)
    "011170": ["%LOTTE CHEMICAL%"],        # 롯데케미칼
    "009830": ["%HANWHA SOLUTIONS%"],      # 한화솔루션
    "247540": ["%ECOPRO BM%"],             # 에코프로비엠
    # 자동차/부품
    "005380": ["%HYUNDAI MOTOR%"],         # 현대자동차
    "000270": ["%KIA MOTORS%", "%KIA CORP%"],  # 기아 (2021 KIA MOTORS→KIA CORP 사명변경)
    "012330": ["%HYUNDAI MOBIS%"],         # 현대모비스
    "011210": ["%HYUNDAI WIA%"],           # 현대위아
    "204320": ["%MANDO%"],                 # HL만도 (MANDO/HL MANDO)
    "018880": ["%HANON SYSTEMS%"],         # 한온시스템
    # SK 에너지/바이오
    "096770": ["%SK INNOVATION%"],         # SK이노베이션
    "302440": ["%SK BIOSCIENCE%"],         # SK바이오사이언스
    # 인터넷/게임
    "035420": ["%NAVER%"],                 # NAVER (CORP/LABS/WEBTOON/CLOUD)
    "035720": ["%KAKAO%"],                 # 카카오
    "251270": ["%NETMARBLE%"],             # 넷마블
    "036570": ["%NCSOFT%"],                # 엔씨소프트
    # 제약/바이오
    "128940": ["%HANMI PHARM%"],           # 한미약품 (한미반도체와 구분)
    "185750": ["%CHONG KUN DANG%"],        # 종근당
    "068270": ["%CELLTRION%"],             # 셀트리온
    "000100": ["%YUHAN%"],                 # 유한양행
    # 기타
    "228850": ["%RAYENCE%"],               # 레이언스
    "299030": ["%HANATECH%"],              # 하나기술
    "036690": ["%COMMAX%"],                # 코맥스
    # 2026-07-10 확대(BigQuery assignee_harmonized 실측 검증): 미보유 대형 특허 출원인 12종목 추가.
    # 계열사 오염 회피 위해 정확 부분문자열로 좁힘(POSCO=퓨처엠/DX/인터/E&C 제외, KT=KT&G/DKT 제외,
    # 한국전력=산업개발 제외, 삼성전기=삼성전자와 구분).
    "373220": ["%LG ENERGY SOLUTION%"],        # LG에너지솔루션
    "015760": ["%KOREA ELECTRIC POWER CORP%"], # 한국전력(KEPCO)
    "010140": ["%SAMSUNG HEAVY%"],             # 삼성중공업
    "009150": ["%SAMSUNG ELECTRO MECH%"],      # 삼성전기
    "005490": ["POSCO", "%POSCO CO LTD%", "%POSCO HOLDINGS%"],  # 포스코홀딩스(계열사 제외)
    "030200": ["KT CORP%"],                    # KT
    "033780": ["%KT & G%"],                    # KT&G
    "004020": ["%HYUNDAI STEEL%"],             # 현대제철
    "272210": ["%HANWHA SYSTEMS%"],            # 한화시스템
    "042660": ["%HANWHA OCEAN%", "%DAEWOO SHIPBUILDING%"],  # 한화오션(구 대우조선해양)
    "017670": ["%SK TELECOM%"],                # SK텔레콤
    "010120": ["%LS ELECTRIC%"],               # LS ELECTRIC
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


# 두 수집 모드가 공유하는 SELECT 투영(어느 날짜로 거르든 결과 행 모양은 동일).
_ROW_PROJECTION = """
      application_number,
      filing_date,
      publication_number,
      publication_date,
      (SELECT t.text FROM UNNEST(title_localized) t
         ORDER BY CASE LOWER(t.language) WHEN 'ko' THEN 0 WHEN 'en' THEN 1 ELSE 2 END
         LIMIT 1) AS title,
      (SELECT i.code FROM UNNEST(ipc) i LIMIT 1) AS ipc_code,
      ARRAY(SELECT a.name FROM UNNEST(assignee_harmonized) a) AS assignees
"""


def _bq_client(project: str):
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        raise SystemExit(
            "google-cloud-bigquery is not installed — run via "
            "`uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py ...`"
        )
    try:
        return bigquery, bigquery.Client(project=project)
    except Exception as exc:  # DefaultCredentialsError 등
        raise SystemExit(
            f"BigQuery client init failed ({exc}).\n"
            "Run `gcloud auth application-default login` (project patent-bq-reader) first."
        )


def _rows_from(result) -> list[dict]:
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
        for r in result
    ]


def bq_rows(*, start_year: int, end_year: int, patterns: list[str], project: str) -> list[dict]:
    """과거 대량 백필: **출원연도(filing_date)** 범위로 한 번에 스캔. 종목 귀속은
    나중에 파이썬에서. (장기 출원 추이용 이력 적재 경로)"""
    bigquery, client = _bq_client(project)
    sql = f"""
    SELECT{_ROW_PROJECTION}
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
    return _rows_from(client.query(sql, job_config=job_config).result())


def bq_rows_recent_publications(
    *, start: int, end: int, patterns: list[str], project: str
) -> list[dict]:
    """매일 최근 **공개분(publication_date)** 만 저비용으로 스캔.

    특허는 출원 후 ~18개월 뒤 공개되므로, "오늘 새로 시장에 노출된 특허"는
    publication_date 가 최근인 행이다(출원일은 1.5년 전). 좁은 공개일 창(예: 최근
    90일)만 조회해 스캔 비용을 낮추고, 이미 적재된 행은 source_hash dedup 으로
    건너뛴다. ``start``/``end`` 는 YYYYMMDD 정수(공개일 창)."""
    bigquery, client = _bq_client(project)
    sql = f"""
    SELECT{_ROW_PROJECTION}
    FROM `{BQ_TABLE}`
    WHERE country_code = 'KR'
      AND publication_date BETWEEN @start AND @end
      AND EXISTS (
        SELECT 1 FROM UNNEST(assignee_harmonized) a, UNNEST(@patterns) p
        WHERE UPPER(a.name) LIKE p
      )
    """.strip()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("patterns", "STRING", [p.upper() for p in patterns]),
            bigquery.ScalarQueryParameter("start", "INT64", start),
            bigquery.ScalarQueryParameter("end", "INT64", end),
        ]
    )
    return _rows_from(client.query(sql, job_config=job_config).result())


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
