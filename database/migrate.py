"""Signal α 마이그레이션 러너.

database/migrations/*.sql 을 파일명 순서로 적용하고 schema_migrations 원장에 기록한다.

규칙:
- 적용된 마이그레이션 파일은 절대 수정하지 않는다 (checksum 검증으로 차단됨).
  스키마 변경은 항상 새 파일로 추가한다.
- 새 마이그레이션 파일명은 **타임스탬프 접두사**를 쓴다: ``YYYYMMDD_HHMM_<이름>.sql``.
  (정수 순번 NNN_ 은 브랜치 병렬 작업 시 충돌하므로 신규 생성 중단 — 레거시 001~023 은 동결.)
  파일은 사전순 정렬이라 ``0xx``(레거시) → ``YYYYMMDD...``(신규) 순으로 적용된다.
  새 파일은 ``new`` 명령으로 만든다(번호를 직접 고르지 말 것).
- 파일당 한 트랜잭션: 마이그레이션 SQL 실행과 원장 INSERT가 함께 커밋된다.
- seeds/*.sql 은 원장에 기록하지 않는다. 시드는 ON CONFLICT 기반으로
  재실행 가능(idempotent)해야 한다.

사용법:
    python database/migrate.py status
    python database/migrate.py new "add fx source"      # 타임스탬프 마이그레이션 생성
    python database/migrate.py apply [--dry-run] [--seeds]
    python database/migrate.py apply --database-url postgresql://user:pw@host:5432/db

DATABASE_URL 결정 순서: --database-url > 환경변수 DATABASE_URL > 루트 .env 파일.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg2

ROOT_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SEEDS_DIR = Path(__file__).resolve().parent / "seeds"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_database_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = _load_env_file(ROOT_DIR / ".env")
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]
    raise SystemExit(
        "DATABASE_URL을 찾을 수 없습니다. --database-url 옵션, "
        "환경변수, 또는 루트 .env 파일로 지정하세요."
    )


def list_sql_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.sql"), key=lambda p: p.name)


def checksum_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_ledger(conn) -> dict[str, str]:
    """적용된 {filename: checksum}. 원장 테이블이 없으면 생성."""
    with conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        rows = cur.fetchall()
    conn.commit()
    return {filename: checksum for filename, checksum in rows}


def verify_applied(applied: dict[str, str], files: list[Path]) -> list[Path]:
    """checksum 검증 후 미적용 파일 목록 반환."""
    by_name = {path.name: path for path in files}
    pending: list[Path] = []

    missing = sorted(set(applied) - set(by_name))
    if missing:
        raise SystemExit(
            "원장에 기록됐지만 migrations/ 에 없는 파일이 있습니다: "
            + ", ".join(missing)
            + "\n(파일 삭제·리네임 금지. 베이스라인 재구성 시 DB를 재생성하세요.)"
        )

    for path in files:
        if path.name in applied:
            actual = checksum_of(path)
            if actual != applied[path.name]:
                raise SystemExit(
                    f"checksum 불일치: {path.name}\n"
                    f"  원장:   {applied[path.name]}\n"
                    f"  파일:   {actual}\n"
                    "적용된 마이그레이션 파일은 수정할 수 없습니다. "
                    "변경은 새 NNN_*.sql 파일로 추가하세요."
                )
        else:
            pending.append(path)
    return pending


def apply_migrations(conn, pending: list[Path], dry_run: bool) -> None:
    if not pending:
        print("적용할 마이그레이션이 없습니다 (최신 상태).")
        return

    for path in pending:
        if dry_run:
            print(f"[dry-run] {path.name}")
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                    (path.name, checksum_of(path)),
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise SystemExit(f"마이그레이션 실패: {path.name}\n{exc}") from exc
        print(f"적용 완료: {path.name}")


def apply_seeds(conn, dry_run: bool) -> None:
    seed_files = list_sql_files(SEEDS_DIR)
    if not seed_files:
        print("시드 파일이 없습니다.")
        return
    for path in seed_files:
        if dry_run:
            print(f"[dry-run] seed {path.name}")
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise SystemExit(f"시드 실패: {path.name}\n{exc}") from exc
        print(f"시드 완료: {path.name}")


def cmd_status(conn) -> None:
    files = list_sql_files(MIGRATIONS_DIR)
    applied = fetch_ledger(conn)
    pending = verify_applied(applied, files)
    for path in files:
        marker = "pending" if path in pending else "applied"
        print(f"  [{marker}] {path.name}")
    print(f"\n적용 {len(files) - len(pending)} / 전체 {len(files)} (미적용 {len(pending)})")


def cmd_apply(conn, dry_run: bool, seeds: bool) -> None:
    files = list_sql_files(MIGRATIONS_DIR)
    applied = fetch_ledger(conn)
    pending = verify_applied(applied, files)
    apply_migrations(conn, pending, dry_run)
    if seeds:
        apply_seeds(conn, dry_run)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip().lower()).strip("_")
    return slug or "migration"


def cmd_new(name: str | None) -> None:
    """타임스탬프 접두사로 새 마이그레이션 파일(빈 템플릿)을 만든다 — DB 접속 불필요."""
    if not name:
        raise SystemExit('이름이 필요합니다. 예: python database/migrate.py new "add fx source"')
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    path = MIGRATIONS_DIR / f"{timestamp}_{_slugify(name)}.sql"
    if path.exists():
        raise SystemExit(f"이미 존재합니다: {path.name}")
    template = (
        f"-- {path.name}\n"
        "-- ============================================================================\n"
        f"-- {name}\n"
        "-- ----------------------------------------------------------------------------\n"
        "-- 배경:\n"
        "-- 설계:\n"
        "-- ============================================================================\n"
        "\n"
        "-- 멱등(ON CONFLICT / IF NOT EXISTS)하게 작성. 적용 후에는 이 파일을 수정하지 말 것\n"
        "-- (checksum 검증). 변경은 새 마이그레이션으로 추가한다.\n"
    )
    path.write_text(template, encoding="utf-8", newline="\n")
    print(f"생성: database/migrations/{path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal α DB 마이그레이션 러너")
    parser.add_argument("command", choices=["status", "apply", "new"])
    parser.add_argument("name", nargs="?", help='new 명령의 마이그레이션 이름 (예: "add fx source")')
    parser.add_argument("--dry-run", action="store_true", help="적용 대상만 출력")
    parser.add_argument("--seeds", action="store_true", help="마이그레이션 후 seeds/*.sql 실행")
    parser.add_argument("--database-url", default=None, help="DATABASE_URL 직접 지정")
    args = parser.parse_args()

    if args.command == "new":
        cmd_new(args.name)
        return

    database_url = resolve_database_url(args.database_url)
    conn = psycopg2.connect(database_url)
    try:
        if args.command == "status":
            cmd_status(conn)
        else:
            cmd_apply(conn, dry_run=args.dry_run, seeds=args.seeds)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
