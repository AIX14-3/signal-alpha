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
    python database/migrate.py new "add fx source"                 # 타임스탬프 마이그레이션 생성
    python database/migrate.py new "payments col" --target backend  # 백엔드 DB 대상
    python database/migrate.py apply [--dry-run] [--seeds]
    python database/migrate.py apply --database-url postgresql://user:pw@host:5432/db

2-DB 물리 분리(#531) — ``--target`` 로 대상 DB를 고른다(파일 헤더 ``-- target:`` 지시자 기준):
    python database/migrate.py apply --target collection --database-url <수집 DB DSN>
    python database/migrate.py apply --target backend    --database-url <백엔드 DB DSN>
기본값 ``--target all`` 은 모든 파일을 한 DB에 적용(레거시 단일 DB — 하위호환).

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

# 2-DB 물리 분리(#531): 각 마이그레이션은 대상 DB를 헤더 지시자로 선언한다.
#   ``-- target: collection`` (수집 DB, 워커 소유) — 미선언 시 기본값.
#   ``-- target: backend``    (백엔드/서비스 DB — 회원·관리자·결제 + 발행 산출물).
#   ``-- target: all``        (양쪽 DB 모두, 단일 DB 모드 포함).
# ``apply --target X`` 는 ``X`` 또는 ``all`` 로 표시된 파일만 적용한다.
# 레거시 단일 DB 운영은 ``--target all``(기본)로 전부 적용 — 하위호환.
TARGETS = ("collection", "backend", "all")
_TARGET_RE = re.compile(r"^--\s*target:\s*(collection|backend|all)\s*$", re.MULTILINE)


def parse_target(path: Path) -> str:
    """마이그레이션 파일 헤더의 ``-- target:`` 지시자. 미선언 시 ``collection``."""
    head = path.read_text(encoding="utf-8")[:2000]
    match = _TARGET_RE.search(head)
    return match.group(1) if match else "collection"


def filter_by_target(files: list[Path], target: str) -> list[Path]:
    """``--target`` 선택에 맞는 파일만 남긴다. ``all`` 은 전부 통과(단일 DB 모드)."""
    if target == "all":
        return files
    return [p for p in files if parse_target(p) in (target, "all")]

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


def resolve_database_url(cli_url: str | None, target: str = "all") -> str:
    """대상 DB(target)에 맞는 DSN을 해석한다.

    - ``--target backend``: ``BACKEND_MIGRATE_DATABASE_URL`` → ``BACKEND_DATABASE_URL`` 순.
    - 그 외(collection/all): ``DATABASE_URL`` (기존 동작 유지).
    우선순위: ``--database-url`` > 환경변수 > 루트 ``.env`` 파일.
    """
    if cli_url:
        return cli_url
    candidates = (
        ["BACKEND_MIGRATE_DATABASE_URL", "BACKEND_DATABASE_URL"]
        if target == "backend"
        else ["DATABASE_URL"]
    )
    for name in candidates:
        if os.environ.get(name):
            return os.environ[name]
    env = _load_env_file(ROOT_DIR / ".env")
    for name in candidates:
        if env.get(name):
            return env[name]
    raise SystemExit(
        f"{' / '.join(candidates)} 을(를) 찾을 수 없습니다. --database-url 옵션, "
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
                    "변경은 python database/migrate.py new 로 새 타임스탬프 파일에 추가하세요."
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


def apply_seeds(conn, dry_run: bool, target: str = "all") -> None:
    # 시드도 마이그레이션과 동일한 ``-- target:`` 지시자로 대상 DB를 가린다(#531 2-DB 분리).
    # ``--target backend`` 면 backend/all 시드만, ``--target collection`` 이면 collection/all 만 적용.
    seed_files = filter_by_target(list_sql_files(SEEDS_DIR), target)
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


def cmd_status(conn, target: str = "all") -> None:
    # 무결성(checksum·missing)은 항상 전체 파일 기준. target 은 표시 범위만 좁힌다.
    all_files = list_sql_files(MIGRATIONS_DIR)
    applied = fetch_ledger(conn)
    pending_all = set(verify_applied(applied, all_files))
    files = filter_by_target(all_files, target)
    pending = [path for path in files if path in pending_all]
    for path in files:
        marker = "pending" if path in pending_all else "applied"
        print(f"  [{marker}] {path.name}  ({parse_target(path)})")
    scope = "전체" if target == "all" else f"target={target}"
    print(f"\n적용 {len(files) - len(pending)} / {scope} {len(files)} (미적용 {len(pending)})")


def cmd_apply(conn, dry_run: bool, seeds: bool, target: str = "all") -> None:
    # 무결성 검증은 전체 파일 기준(원장 ⊆ 전체). 적용 대상만 target 으로 필터.
    all_files = list_sql_files(MIGRATIONS_DIR)
    applied = fetch_ledger(conn)
    pending = filter_by_target(verify_applied(applied, all_files), target)
    apply_migrations(conn, pending, dry_run)
    if seeds:
        apply_seeds(conn, dry_run, target)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip().lower()).strip("_")
    return slug or "migration"


def cmd_new(name: str | None, target: str = "collection") -> None:
    """타임스탬프 접두사로 새 마이그레이션 파일(빈 템플릿)을 만든다 — DB 접속 불필요."""
    if not name:
        raise SystemExit('이름이 필요합니다. 예: python database/migrate.py new "add fx source"')
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    path = MIGRATIONS_DIR / f"{timestamp}_{_slugify(name)}.sql"
    if path.exists():
        raise SystemExit(f"이미 존재합니다: {path.name}")
    template = (
        f"-- {path.name}\n"
        f"-- target: {target}\n"
        "-- ============================================================================\n"
        f"-- {name}\n"
        "-- ----------------------------------------------------------------------------\n"
        "-- 배경:\n"
        "-- 설계:\n"
        "-- ============================================================================\n"
        "\n"
        "-- 작성 규칙: 적용 여부는 schema_migrations 원장이 관리하므로 IF NOT EXISTS를 쓰지 않는다.\n"
        "-- 시드 데이터가 필요하면 seeds/에 분리하고 ON CONFLICT로 재실행 가능하게 작성한다.\n"
        "-- 적용 후에는 이 파일을 수정하지 말 것(checksum 검증). 변경은 새 마이그레이션으로 추가한다.\n"
    )
    path.write_text(template, encoding="utf-8", newline="\n")
    print(f"생성: database/migrations/{path.name}  (target={target})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal α DB 마이그레이션 러너")
    parser.add_argument("command", choices=["status", "apply", "new"])
    parser.add_argument("name", nargs="?", help='new 명령의 마이그레이션 이름 (예: "add fx source")')
    parser.add_argument("--dry-run", action="store_true", help="적용 대상만 출력")
    parser.add_argument("--seeds", action="store_true", help="마이그레이션 후 seeds/*.sql 실행")
    parser.add_argument("--database-url", default=None, help="DATABASE_URL 직접 지정")
    parser.add_argument(
        "--target",
        choices=TARGETS,
        default="all",
        help="대상 DB (#531 2-DB 분리). collection|backend|all. 기본 all=단일 DB 전부 적용",
    )
    args = parser.parse_args()

    if args.command == "new":
        # new 의 기본 target 은 collection(워커가 다수). 백엔드용은 --target backend.
        cmd_new(args.name, target="collection" if args.target == "all" else args.target)
        return

    database_url = resolve_database_url(args.database_url, target=args.target)
    conn = psycopg2.connect(database_url)
    try:
        if args.command == "status":
            cmd_status(conn, target=args.target)
        else:
            cmd_apply(conn, dry_run=args.dry_run, seeds=args.seeds, target=args.target)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
