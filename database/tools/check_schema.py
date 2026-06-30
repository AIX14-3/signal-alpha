"""Signal α 스키마 드리프트 검사.

실제 DB가 database/migrations/ 정의와 일치하는지 검증한다.

동작:
1. 대상 DB와 같은 서버에 임시 DB(signal_alpha_schema_check)를 생성하고
   마이그레이션 전체를 새로 적용해 "기준 스키마"를 만든다.
2. 기준 DB와 대상 DB의 information_schema(테이블/컬럼/타입/NULL), pg_indexes,
   제약 조건을 비교해 차이를 표로 출력한다.
3. 드리프트가 있으면 exit 1. 종료 시 임시 DB는 삭제한다(--keep으로 보존).

사용법:
    python database/tools/check_schema.py [--database-url URL] [--keep]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migrate import (  # noqa: E402
    apply_migrations,
    fetch_ledger,
    list_sql_files,
    resolve_database_url,
    verify_applied,
    MIGRATIONS_DIR,
)

CHECK_DB_NAME = "signal_alpha_schema_check"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def replace_db_name(database_url: str, db_name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


def recreate_check_db(admin_url: str) -> None:
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(CHECK_DB_NAME)
                )
            )
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(CHECK_DB_NAME)))
    finally:
        conn.close()


def drop_check_db(admin_url: str) -> None:
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(CHECK_DB_NAME)
                )
            )
    finally:
        conn.close()


def snapshot_schema(database_url: str) -> dict:
    """public 스키마의 테이블/컬럼/인덱스/제약 스냅샷."""
    conn = psycopg2.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                  AND table_name != 'schema_migrations'
                """
            )
            tables = {row[0] for row in cur.fetchall()}

            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable,
                       COALESCE(character_maximum_length::text, ''),
                       COALESCE(numeric_precision::text, ''),
                       COALESCE(numeric_scale::text, '')
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name != 'schema_migrations'
                """
            )
            columns = {
                (r[0], r[1]): (r[2], r[3], r[4], r[5], r[6]) for r in cur.fetchall()
            }

            cur.execute(
                """
                SELECT tablename, indexname FROM pg_indexes
                WHERE schemaname = 'public' AND tablename != 'schema_migrations'
                """
            )
            indexes = {(r[0], r[1]) for r in cur.fetchall()}

            cur.execute(
                """
                SELECT rel.relname, con.conname, con.contype
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                WHERE ns.nspname = 'public' AND rel.relname != 'schema_migrations'
                """
            )
            constraints = {(r[0], r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
    }


def build_reference_schema(check_url: str) -> None:
    conn = psycopg2.connect(check_url)
    try:
        files = list_sql_files(MIGRATIONS_DIR)
        applied = fetch_ledger(conn)
        pending = verify_applied(applied, files)
        apply_migrations(conn, pending, dry_run=False)
    finally:
        conn.close()


def diff_schemas(reference: dict, actual: dict) -> list[str]:
    problems: list[str] = []

    for table in sorted(reference["tables"] - actual["tables"]):
        problems.append(f"[missing table]   {table} — 마이그레이션에 있으나 DB에 없음")
    for table in sorted(actual["tables"] - reference["tables"]):
        problems.append(f"[extra table]     {table} — DB에 있으나 마이그레이션에 없음")

    common_tables = reference["tables"] & actual["tables"]

    for (table, column), spec in sorted(reference["columns"].items()):
        if table not in common_tables:
            continue
        if (table, column) not in actual["columns"]:
            problems.append(f"[missing column]  {table}.{column}")
        elif actual["columns"][(table, column)] != spec:
            problems.append(
                f"[column drift]    {table}.{column} — "
                f"기준 {spec} vs 실제 {actual['columns'][(table, column)]}"
            )
    for (table, column) in sorted(set(actual["columns"]) - set(reference["columns"])):
        if table in common_tables:
            problems.append(f"[extra column]    {table}.{column}")

    for (table, index) in sorted(reference["indexes"] - actual["indexes"]):
        if table in common_tables:
            problems.append(f"[missing index]   {table}: {index}")
    for (table, index) in sorted(actual["indexes"] - reference["indexes"]):
        if table in common_tables:
            problems.append(f"[extra index]     {table}: {index}")

    for (table, name, ctype) in sorted(reference["constraints"] - actual["constraints"]):
        if table in common_tables:
            problems.append(f"[missing constr]  {table}: {name} ({ctype})")
    for (table, name, ctype) in sorted(actual["constraints"] - reference["constraints"]):
        if table in common_tables:
            problems.append(f"[extra constr]    {table}: {name} ({ctype})")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal α 스키마 드리프트 검사")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--keep", action="store_true", help="임시 검사 DB를 삭제하지 않음")
    args = parser.parse_args()

    target_url = resolve_database_url(args.database_url)
    check_url = replace_db_name(target_url, CHECK_DB_NAME)

    print(f"기준 스키마 생성: {CHECK_DB_NAME} (마이그레이션 전체 적용)")
    recreate_check_db(target_url)
    try:
        build_reference_schema(check_url)
        reference = snapshot_schema(check_url)
        actual = snapshot_schema(target_url)
        problems = diff_schemas(reference, actual)
    finally:
        if not args.keep:
            drop_check_db(target_url)

    if problems:
        print(f"\n드리프트 {len(problems)}건 발견:\n")
        for line in problems:
            print(f"  {line}")
        print("\n해결: 새 마이그레이션 파일을 추가하거나, 개발 DB라면 재생성하세요.")
        print("  docker compose down -v && docker compose up -d postgres")
        print("  docker compose run --rm db-migrate apply --seeds")
        sys.exit(1)

    print("\n드리프트 없음 — DB가 마이그레이션 정의와 일치합니다.")


if __name__ == "__main__":
    main()
