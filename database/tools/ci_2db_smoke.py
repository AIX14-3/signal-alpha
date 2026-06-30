"""2-DB 분리 스모크 (CI 게이트).

물리 분리(#531)를 시뮬레이션해 **백엔드/수집 마이그가 각자 DB 에 깨끗이 적용**되는지,
그리고 **상대 DB 테이블이 새지 않는지**를 검증한다. H1 류(target 과 실제 대상 DB 불일치 →
'relation does not exist' 적용 실패) 회귀를 CI 에서 직접 차단한다.

전제: 환경변수 ``DATABASE_URL`` 이 슈퍼유저로 접속 가능한 단일 Postgres 를 가리킨다(CI postgres
서비스). 이 스크립트가 같은 서버에 ``sa_coll`` / ``sa_be`` 두 DB 를 새로 만들어 타깃별로 적용한다.

    python database/tools/ci_2db_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2

DB_DIR = Path(__file__).resolve().parents[1]
MIGRATE = DB_DIR / "migrate.py"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COLL_DB = "sa_coll"
BE_DB = "sa_be"

# 각 DB 에 있어야/없어야 하는 대표 테이블(분리 정합 단언용).
COLL_PRESENT = ["analysis_requests", "processing_queue", "raw_documents"]
COLL_ABSENT = ["users", "payments", "admin_accounts"]
BE_PRESENT = ["users", "payments", "stocks"]  # stocks=PUBLISHED(양쪽)
BE_ABSENT = ["analysis_requests", "processing_queue", "raw_documents"]


def _with_db(base_url: str, dbname: str) -> str:
    p = urlsplit(base_url)
    return urlunsplit((p.scheme, p.netloc, f"/{dbname}", p.query, p.fragment))


def _guard_destructive(base_url: str) -> None:
    """안전장치: sa_coll/sa_be 를 DROP/CREATE 하므로 의도치 않은 서버 대상 실행을 막는다.

    localhost/127.0.0.1 또는 CI 환경에서만 무조건 허용. 그 외 호스트는
    ALLOW_2DB_SMOKE=1 로 명시 동의해야 한다(공유/스테이징 DB 실수 방지).
    """
    host = (urlsplit(base_url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return
    if os.environ.get("CI") or os.environ.get("ALLOW_2DB_SMOKE") == "1":
        return
    raise SystemExit(
        f"안전장치: 이 도구는 '{COLL_DB}'/'{BE_DB}' DB 를 DROP/CREATE 합니다. "
        f"대상 호스트가 '{host}' 입니다 — localhost/CI 가 아니면 ALLOW_2DB_SMOKE=1 로 명시 동의하세요."
    )


def _recreate_databases(base_url: str) -> None:
    conn = psycopg2.connect(base_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for db in (COLL_DB, BE_DB):
                # WITH (FORCE): 잔존 연결(풀러·중단된 이전 실행)이 있어도 강제 종료 후 DROP(PG13+).
                cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
                cur.execute(f"CREATE DATABASE {db}")
    finally:
        conn.close()


def _apply(target: str, url: str) -> None:
    env = dict(os.environ)
    # migrate.py DSN 해석: collection→DATABASE_URL, backend→BACKEND_MIGRATE_DATABASE_URL.
    if target == "collection":
        env["DATABASE_URL"] = url
    else:
        env["BACKEND_MIGRATE_DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, str(MIGRATE), "apply", "--seeds", "--target", target],
        env=env,
    )
    if result.returncode != 0:
        sys.exit(f"❌ apply --target {target} 실패 (exit {result.returncode})")
    print(f"✓ apply --target {target} 성공")


def _table_exists(url: str, table: str) -> bool:
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


def _assert_presence(label: str, url: str, present: list[str], absent: list[str]) -> list[str]:
    problems: list[str] = []
    for t in present:
        if not _table_exists(url, t):
            problems.append(f"[{label}] '{t}' 가 있어야 하는데 없음")
    for t in absent:
        if _table_exists(url, t):
            problems.append(f"[{label}] '{t}' 가 없어야 하는데 있음(상대 DB 테이블 누수)")
    return problems


def main() -> None:
    base_url = os.environ.get("DATABASE_URL")
    if not base_url:
        raise SystemExit("DATABASE_URL 환경변수가 필요합니다.")

    _guard_destructive(base_url)
    coll_url = _with_db(base_url, COLL_DB)
    be_url = _with_db(base_url, BE_DB)

    _recreate_databases(base_url)
    _apply("collection", coll_url)
    _apply("backend", be_url)

    problems = (
        _assert_presence("collection", coll_url, COLL_PRESENT, COLL_ABSENT)
        + _assert_presence("backend", be_url, BE_PRESENT, BE_ABSENT)
    )
    if problems:
        print("\n2-DB 분리 정합 문제:")
        for p in problems:
            print(f"  {p}")
        sys.exit(1)
    print("\n✅ 2-DB 분리 스모크 통과 — 양쪽 적용 성공 + 상대 DB 테이블 누수 없음.")


if __name__ == "__main__":
    main()
