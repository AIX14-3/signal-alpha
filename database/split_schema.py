"""백엔드(서비스) DB 그린필드 부트스트랩 (#525/#531 WS-A 스키마 2분할).

pg_dump 없이 **검증된 마이그레이션 경로를 재사용**해 백엔드 DB 를 부트스트랩한다:

  1. 전체 마이그레이션을 적용해 정확한 스키마를 만든다(테스트된 DDL).
  2. ``db_partition`` 분류로 **수집 전용 테이블만 DROP ... CASCADE** 한다
     (백엔드/발행 테이블에서 수집 테이블로 향하는 cross-DB FK 도 CASCADE 가 함께 제거).
  3. 백엔드에 필요한 시드(subscription_plans·stocks)만 관용 적용(없는 테이블 시드는 skip).

결과: 백엔드 DB 는 BACKEND ∪ PUBLISHED 테이블만 보유하고, ``schema_migrations`` 원장은
전부 적용됨으로 채워져 이후 ``migrate.py --target backend`` 는 신규 백엔드 마이그만 적용한다.

사용법(BACKEND_MIGRATE_DATABASE_URL/BACKEND_DATABASE_URL 자동 해석):
    uv run --with psycopg2-binary python database/split_schema.py bootstrap-backend --dry-run
    uv run --with psycopg2-binary python database/split_schema.py bootstrap-backend
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db_partition  # noqa: E402
import migrate  # noqa: E402


def all_public_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {row[0] for row in cur.fetchall()}


def apply_backend_seeds(conn, dry_run: bool) -> list[str]:
    """시드를 관용 적용 — 트림으로 사라진 테이블 대상 시드는 건너뛴다(백엔드엔 무관)."""
    applied: list[str] = []
    for path in migrate.list_sql_files(migrate.SEEDS_DIR):
        if dry_run:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            applied.append(path.name)
        except psycopg2.Error as exc:
            conn.rollback()
            # UndefinedTable(42P01) = 수집 전용 테이블(이미 DROP) → 백엔드선 skip.
            if getattr(exc, "pgcode", None) == "42P01":
                print(f"  시드 skip(수집 전용): {path.name}")
            else:
                raise SystemExit(f"시드 실패: {path.name}\n{exc}") from exc
    return applied


def bootstrap_backend(conn, *, with_seeds: bool = True, dry_run: bool = False) -> dict:
    # 1. 전체 스키마(테스트된 마이그레이션 DDL).
    files = migrate.list_sql_files(migrate.MIGRATIONS_DIR)
    applied = migrate.fetch_ledger(conn)
    pending = migrate.verify_applied(applied, files)
    print(f"[1/3] 마이그레이션 적용: 미적용 {len(pending)} / 전체 {len(files)}")
    migrate.apply_migrations(conn, pending, dry_run)

    # 2. 수집 전용 테이블 분류 + 트림.
    actual = all_public_tables(conn)
    for issue in db_partition.validate(actual):
        print(f"  ⚠ 분류 점검: {issue}")
    drop = sorted(db_partition.collection_only(actual))
    keep = sorted((actual & db_partition.backend_keep()) | {db_partition.LEDGER_TABLE})
    print(f"[2/3] 트림: 보존 {len(keep)} / 제거(수집 전용) {len(drop)}")
    if dry_run:
        print("  [dry-run] DROP 대상:", ", ".join(drop) or "(없음)")
    elif drop:
        stmt = "DROP TABLE IF EXISTS " + ", ".join(f"public.{t}" for t in drop) + " CASCADE"
        with conn.cursor() as cur:
            cur.execute(stmt)
        conn.commit()

    # 3. 백엔드 관련 시드.
    seeded: list[str] = []
    if with_seeds:
        print("[3/3] 시드(백엔드 관련만)")
        seeded = apply_backend_seeds(conn, dry_run)

    remaining = sorted(all_public_tables(conn)) if not dry_run else keep
    return {"kept": remaining, "dropped": drop, "seeded": seeded}


def main() -> None:
    parser = argparse.ArgumentParser(description="백엔드 DB 그린필드 부트스트랩 (WS-A 스키마 2분할)")
    parser.add_argument("command", choices=["bootstrap-backend"])
    parser.add_argument("--dry-run", action="store_true", help="적용 없이 계획만 출력")
    parser.add_argument("--no-seeds", action="store_true", help="시드 생략")
    parser.add_argument("--database-url", default=None, help="백엔드 DB DSN 직접 지정")
    args = parser.parse_args()

    # backend 대상 DSN 자동 해석(BACKEND_MIGRATE_DATABASE_URL → BACKEND_DATABASE_URL).
    database_url = migrate.resolve_database_url(args.database_url, target="backend")
    host = database_url.split("@")[-1].split("/")[0]
    print(f"대상 백엔드 DB: {host}")
    conn = psycopg2.connect(database_url)
    try:
        report = bootstrap_backend(conn, with_seeds=not args.no_seeds, dry_run=args.dry_run)
    finally:
        conn.close()
    print(f"\n완료. 보존 테이블 {len(report['kept'])}개, 제거 {len(report['dropped'])}개, "
          f"시드 {len(report['seeded'])}개.")
    print("보존:", ", ".join(report["kept"]))


if __name__ == "__main__":
    main()
