"""schema.sql 스냅샷 드리프트 가드.

`database/schema.sql` 는 "전체 마이그 적용 결과"의 권위 스냅샷이다(읽기용 단일 소스).
베이스라인/마이그를 바꾼 뒤 이 스냅샷 재생성을 빠뜨리면 조용히 낡는다 → CI 에서 막는다.

동작: 대상 DB(전체 마이그 적용된 단일 DB)를 `pg_dump --schema-only --no-owner --no-privileges`
(=schema.sql 생성과 동일 옵션)로 떠서, 커밋된 schema.sql 과 **정규화 비교**한다.
버전/`\restrict` 토큰/주석 등 비결정적 부분은 정규화로 제거한다. 불일치 시 diff 출력 + exit 1.

사용법:
    python database/tools/check_schema_snapshot.py [--database-url URL] \
        [--pg-dump "docker run --rm --network=host pgvector/pgvector:pg16 pg_dump"]
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

DB_DIR = Path(__file__).resolve().parents[1]
SCHEMA_SQL = DB_DIR / "schema.sql"

sys.path.insert(0, str(DB_DIR))
from migrate import resolve_database_url  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def normalize(dump: str) -> list[str]:
    """비결정적/장식적 라인 제거 후 의미 라인 목록 반환.

    - psql 메타명령 ``\\restrict`` / ``\\unrestrict`` (덤프마다 랜덤 토큰).
    - 주석 라인(``--`` 시작, pg_dump 버전·``-- Name:`` TOC 헤더 포함).
    - 공백 라인.

    비교는 이 라인들의 **다중집합(순서 무관)** 으로 한다 — pg_dump 버전/객체 생성순서에
    따라 객체 출력 순서가 달라져도(같은 스키마면 라인 구성은 동일) 오탐이 없도록.
    """
    out: list[str] = []
    for raw in dump.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("\\restrict") or stripped.startswith("\\unrestrict"):
            continue
        if stripped.startswith("--"):
            continue
        out.append(line)
    return out


def run_pg_dump(database_url: str, pg_dump_cmd: str) -> str:
    cmd = shlex.split(pg_dump_cmd) + [
        database_url,
        "--schema-only",
        "--no-owner",
        "--no-privileges",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(
            f"'{shlex.split(pg_dump_cmd)[0]}' 실행 불가. pg_dump(PG16+) 를 PATH 에 두거나 "
            "--pg-dump \"docker run --rm --network=host pgvector/pgvector:pg16 pg_dump\" 로 지정하세요."
        )
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump 실패(코드 {proc.returncode}):\n{proc.stderr}")
    return proc.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description="schema.sql 스냅샷 드리프트 가드")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--pg-dump", default="pg_dump")
    args = parser.parse_args()

    database_url = resolve_database_url(args.database_url, target="all")
    committed = Counter(normalize(SCHEMA_SQL.read_text(encoding="utf-8")))
    fresh = Counter(normalize(run_pg_dump(database_url, args.pg_dump)))

    if committed == fresh:
        print("schema.sql 스냅샷 OK — 마이그 적용 결과와 일치(순서 무관 비교).")
        return

    only_committed = committed - fresh  # schema.sql 에만 있는 라인(마이그에서 사라짐)
    only_fresh = fresh - committed  # 마이그엔 있는데 schema.sql 엔 없는 라인(재생성 누락)
    print("schema.sql 가 마이그레이션과 어긋납니다(스냅샷 재생성 누락?).\n")

    def _dump(title: str, counter: Counter) -> None:
        items = sorted(counter.elements())
        print(f"--- {title} ({len(items)}줄) ---")
        for line in items[:60]:
            print(f"  {line}")
        if len(items) > 60:
            print(f"  ... (+{len(items) - 60}줄)")

    _dump("schema.sql 에만 있음(제거 필요?)", only_committed)
    _dump("마이그엔 있는데 schema.sql 엔 없음(재생성 필요)", only_fresh)
    print(
        "\n재생성: docker run --rm --network=host pgvector/pgvector:pg16 pg_dump "
        "--schema-only --no-owner --no-privileges \"$DATABASE_URL\" > database/schema.sql"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
