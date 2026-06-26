"""타깃별 baseline 생성기 (2-인스턴스 분리 재베이스라인, #531 재정리).

알려진-정상(전체 마이그 적용된) DB 의 **현재 스키마를 떠서**(pg_dump) 객체 단위로
collection / backend / published(all) 로 분류해 깔끔한 baseline 마이그레이션을 만든다.
손으로 52개 테이블 DDL 을 재작성하지 않아 **스키마 드리프트가 없다**(실제 스키마가 정답).

왜 per-table pg_dump 가 아니라 전체-스키마 파싱인가:
  - ENUM 타입(CREATE TYPE)·트리거 함수(CREATE FUNCTION)·확장은 **스키마 전역** 객체라
    ``pg_dump -t <table>`` 가 빠뜨린다 → 트리거가 없는 함수를 참조해 적용 실패.
  - 그래서 ``pg_dump --schema-only -n public`` 한 번으로 전부 받아 TOC 헤더
    (``-- Name: ...; Type: ...;``)로 객체를 끊어 분류한다.

분류 규칙(단일 출처 = database/db_partition.py):
  - TABLE/INDEX/TRIGGER/CONSTRAINT/DEFAULT/SEQUENCE → **소유 테이블의 타깃**.
      published(PUBLISHED) → all,  backend(BACKEND) → backend,  그 외 → collection.
  - TYPE/DOMAIN/FUNCTION/EXTENSION/SCHEMA 등 전역 객체 → **all**(양쪽 DB 에 무해하게 존재,
    어느 쪽 트리거든 함수 참조 가능).
  - **FK CONSTRAINT**: 2-인스턴스엔 cross-DB FK 가 불가하므로, 소유 테이블의 DB 에 **참조 대상
    테이블이 존재할 때만** 유지하고 아니면 버린다(컬럼은 남고 FK 제약만 제거 — 앱레벨 publisher 가
    정합성 담당). 허용집합: collection→(collection∪published), backend→(backend∪published),
    published(all)→published.

사용법:
    # 미리보기(파일 안 씀): 객체→타깃 분류 요약만 출력
    uv run --with psycopg2-binary python database/rebaseline.py --source-url <정상 DB DSN>
    # 실제 생성: 0002/0003/0004 작성 + 레거시 마이그 archive/ 이동
    uv run --with psycopg2-binary python database/rebaseline.py --source-url <DSN> --apply

생성 후: 0001/0005/0005b/0006/0007(정적, 이미 커밋) + 0002/0003/0004(생성) = 7개 baseline.
검증 절차는 docs/runbooks/db-2-instance-bootstrap.md 참고. **DB 리셋 전에 반드시 미리보기로
분류를 확인**할 것.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db_partition  # noqa: E402

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ARCHIVE_DIR = MIGRATIONS_DIR / "archive"

# 생성하지 않고 보존하는 정적 baseline(손으로 작성, 커밋됨). 그 외 migrations/*.sql 은 레거시.
STATIC_BASELINE = {
    "0001_infra_roles.sql",
    "0002_published_baseline.sql",   # 이 스크립트가 생성(아래 GENERATED 와 동일)
    "0003_collection_baseline.sql",  # 이 스크립트가 생성
    "0004_backend_baseline.sql",     # 이 스크립트가 생성
    "0005_api_read_contract.sql",
    "0005b_api_pipeline_status_collection.sql",
    "0006_collection_grants.sql",
    "0007_backend_grants.sql",
}
GENERATED = {
    "published": "0002_published_baseline.sql",
    "collection": "0003_collection_baseline.sql",
    "backend": "0004_backend_baseline.sql",
}
GLOBAL_TYPES = {  # 전역 객체 Type → all 로 보냄
    "TYPE", "DOMAIN", "FUNCTION", "PROCEDURE", "AGGREGATE", "EXTENSION",
    "SCHEMA", "OPERATOR", "CAST", "COLLATION", "FUNCTION SET",
}
# 테이블 종속 객체 Type. 소유 테이블의 타깃을 따른다.
TABLE_TYPES = {
    "TABLE", "INDEX", "TRIGGER", "CONSTRAINT", "FK CONSTRAINT", "CHECK CONSTRAINT",
    "DEFAULT", "SEQUENCE", "SEQUENCE OWNED BY", "SEQUENCE SET", "MATERIALIZED VIEW",
    "TABLE ATTACH", "INDEX ATTACH",
}
SKIP_TYPES = {"ACL", "COMMENT", "OWNER", "PUBLICATION", "SUBSCRIPTION"}

_TOC_RE = re.compile(r"^-- Name: (?P<name>.+?); Type: (?P<type>[^;]+); Schema: (?P<schema>[^;]+);", re.M)
_TABLE_FROM_SQL = [
    re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(?:public\.)?\"?(\w+)\"?", re.I),
    re.compile(r"ALTER TABLE(?: ONLY)?\s+(?:public\.)?\"?(\w+)\"?", re.I),
    re.compile(r"CREATE(?: UNIQUE)? INDEX[^;]*?\s+ON\s+(?:public\.)?\"?(\w+)\"?", re.I),
    re.compile(r"CREATE TRIGGER[^;]*?\s+ON\s+(?:public\.)?\"?(\w+)\"?", re.I),
]
_OWNED_BY = re.compile(r"OWNED BY\s+(?:public\.)?\"?(\w+)\"?\.", re.I)
_FK_REF = re.compile(r"REFERENCES\s+(?:public\.)?\"?(\w+)\"?", re.I)
# 시퀀스 → 소유 테이블 매핑용. SERIAL 의 'ALTER SEQUENCE x OWNED BY public.t.col' /
# 'ALTER COLUMN c SET DEFAULT nextval(''public.x''...)' 에서 seq↔table 을 끌어낸다.
_SEQ_OWNED = re.compile(r"ALTER SEQUENCE\s+(?:public\.)?\"?(\w+)\"?\s+OWNED BY\s+(?:public\.)?\"?(\w+)\"?\.", re.I)
_SEQ_DEFAULT = re.compile(r"ALTER TABLE(?: ONLY)?\s+(?:public\.)?\"?(\w+)\"?[\s\S]*?nextval\('(?:public\.)?\"?(\w+)\"?", re.I)


class Block:
    __slots__ = ("name", "type", "schema", "sql", "table")

    def __init__(self, name, type_, schema, sql):
        self.name = name
        self.type = type_
        self.schema = schema
        self.sql = sql
        self.table: str | None = None


def run_pg_dump(source_url: str, pg_dump_cmd: str = "pg_dump") -> str:
    # pg_dump_cmd 는 단순 'pg_dump' 또는 컨테이너 호출 프리픽스
    # (예: 'docker run --rm postgres:16 pg_dump') 도 가능. DSN+옵션을 뒤에 붙인다.
    prefix = shlex.split(pg_dump_cmd)
    cmd = prefix + [
        source_url, "--schema-only", "--no-owner", "--no-privileges",
        "--no-comments", "--schema=public",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        raise SystemExit(
            f"'{prefix[0]}' 실행 불가. pg_dump(PG16+) 를 설치해 PATH 에 두거나, "
            "--pg-dump \"docker run --rm postgres:16 pg_dump\" 처럼 컨테이너로 지정하세요."
        )
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump 실패(코드 {proc.returncode}):\n{proc.stderr}")
    # 최신 pg_dump(PG16.4+)는 psql 메타명령 \restrict/\unrestrict 를 덤프에 넣는다.
    # 이는 SQL 이 아니라 psycopg2 실행 시 깨지므로 제거한다.
    lines = [ln for ln in proc.stdout.splitlines() if not ln.lstrip().startswith("\\")]
    return "\n".join(lines)


def split_blocks(dump: str) -> list[Block]:
    """pg_dump 출력을 TOC 헤더(-- Name: ...; Type: ...;) 단위 블록으로 끊는다."""
    blocks: list[Block] = []
    matches = list(_TOC_RE.finditer(dump))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(dump)
        body = dump[m.start():end].strip()
        blocks.append(Block(m.group("name"), m.group("type").strip(), m.group("schema").strip(), body))
    return blocks


def seq_table_map(blocks: list[Block]) -> dict[str, str]:
    """시퀀스명 → 소유 테이블. OWNED BY / DEFAULT nextval 블록에서 끌어낸다."""
    m: dict[str, str] = {}
    for b in blocks:
        for seq, tbl in _SEQ_OWNED.findall(b.sql):
            m[seq] = tbl
        for tbl, seq in _SEQ_DEFAULT.findall(b.sql):
            m.setdefault(seq, tbl)
    return m


def owning_table(block: Block, seq_map: dict[str, str] | None = None) -> str | None:
    if block.type == "TABLE":
        mm = _TABLE_FROM_SQL[0].search(block.sql)
        return mm.group(1) if mm else block.name.split()[0]
    if "SEQUENCE" in block.type:
        mm = _OWNED_BY.search(block.sql)
        if mm:
            return mm.group(1)
        # 바로 매칭 안 되는 'CREATE SEQUENCE' 블록은 사전 매핑(seq명)으로 해소.
        if seq_map:
            seqname = block.name.split()[0]
            if seqname in seq_map:
                return seq_map[seqname]
    # FK/CONSTRAINT/DEFAULT/INDEX/TRIGGER: SQL 의 ALTER/ON 절에서 테이블 추출.
    for rx in _TABLE_FROM_SQL[1:]:
        mm = rx.search(block.sql)
        if mm:
            return mm.group(1)
    # TOC Name 이 "<table> <objname>" 형태인 경우(제약/인덱스).
    parts = block.name.split()
    return parts[0] if parts else None


def classify(actual_tables: set[str]):
    published = db_partition.PUBLISHED_TABLES & actual_tables
    backend = db_partition.BACKEND_TABLES & actual_tables
    collection = db_partition.collection_only(actual_tables)
    target_of = {}
    for t in published:
        target_of[t] = "published"
    for t in backend:
        target_of[t] = "backend"
    for t in collection:
        target_of[t] = "collection"
    allowed = {
        "published": set(published),
        "collection": set(collection) | set(published),
        "backend": set(backend) | set(published),
    }
    return target_of, allowed


def build(source_url: str, pg_dump_cmd: str = "pg_dump"):
    dump = run_pg_dump(source_url, pg_dump_cmd)
    blocks = split_blocks(dump)
    seq_map = seq_table_map(blocks)
    # 실제 테이블 집합.
    actual = {owning_table(b) for b in blocks if b.type == "TABLE"}
    actual.discard(None)
    actual.discard(db_partition.LEDGER_TABLE)
    target_of, allowed = classify(actual)

    buckets = {"published": [], "collection": [], "backend": []}
    dropped_fks: list[str] = []
    for b in blocks:
        if b.schema != "public" or b.type in SKIP_TYPES:
            continue
        if b.type in GLOBAL_TYPES:
            buckets["published"].append(b.sql)  # all 파일(0002)로
            continue
        tbl = owning_table(b, seq_map)
        if tbl == db_partition.LEDGER_TABLE:
            continue  # schema_migrations 원장은 러너가 생성
        tgt = target_of.get(tbl, "collection")
        if "FK CONSTRAINT" in b.type or (b.type == "CONSTRAINT" and "FOREIGN KEY" in b.sql.upper()):
            ref = _FK_REF.search(b.sql)
            ref_tbl = ref.group(1) if ref else None
            if ref_tbl and ref_tbl not in allowed[tgt]:
                dropped_fks.append(f"{tbl}.FK→{ref_tbl} (target={tgt})")
                continue
        buckets[tgt].append(b.sql)
    return target_of, buckets, dropped_fks


HEADER = {
    "published": (
        "-- 0002_published_baseline.sql\n-- target: all\n"
        "-- ============================================================================\n"
        "-- PUBLISHED 발행 테이블 + 전역 객체(타입/함수/확장). 양쪽 DB 에 적용.\n"
        "-- ※ rebaseline.py 가 생성. 직접 수정 금지 — 스키마 변경은 새 마이그로 추가.\n"
        "-- ============================================================================\n"
    ),
    "collection": (
        "-- 0003_collection_baseline.sql\n-- target: collection\n"
        "-- ============================================================================\n"
        "-- COLLECTION(수집 raw + 워커 파이프라인) 테이블. 수집 DB 에만 적용.\n"
        "-- ※ rebaseline.py 가 생성. cross-DB FK(→backend)는 제거됨.\n"
        "-- ============================================================================\n"
    ),
    "backend": (
        "-- 0004_backend_baseline.sql\n-- target: backend\n"
        "-- ============================================================================\n"
        "-- BACKEND(회원·구독·결제·관리자·유저콘텐츠) 테이블. 백엔드 DB 에만 적용.\n"
        "-- ※ rebaseline.py 가 생성. cross-DB FK(→collection)는 제거됨.\n"
        "-- ============================================================================\n"
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser(description="타깃별 baseline 생성기 (2-DB 재베이스라인)")
    ap.add_argument("--source-url", required=True, help="전체 마이그 적용된 정상 DB DSN(스키마 출처)")
    ap.add_argument("--apply", action="store_true", help="실제 파일 작성 + 레거시 마이그 archive/ 이동")
    ap.add_argument(
        "--pg-dump", default="pg_dump",
        help="pg_dump 실행 명령. 미설치 시 'docker run --rm postgres:16 pg_dump' 처럼 컨테이너 지정.",
    )
    args = ap.parse_args()

    target_of, buckets, dropped_fks = build(args.source_url, args.pg_dump)

    # 분류 요약.
    by_t = {"published": [], "collection": [], "backend": []}
    for t, tgt in sorted(target_of.items()):
        by_t[tgt].append(t)
    print("=== 테이블 분류 ===")
    for tgt in ("published", "collection", "backend"):
        print(f"[{tgt}] {len(by_t[tgt])}개: {', '.join(by_t[tgt]) or '(없음)'}")
    print(f"\n=== 제거된 cross-DB FK {len(dropped_fks)}개 ===")
    for d in dropped_fks:
        print(f"  - {d}")
    print(f"\n=== 생성 블록 수 === published(all)={len(buckets['published'])} "
          f"collection={len(buckets['collection'])} backend={len(buckets['backend'])}")

    if not args.apply:
        print("\n[미리보기] --apply 를 주면 파일을 쓰고 레거시를 archive/ 로 옮깁니다.")
        return

    for tgt, fname in GENERATED.items():
        body = HEADER[tgt] + "\n" + "\n\n".join(buckets[tgt]) + "\n"
        (MIGRATIONS_DIR / fname).write_text(body, encoding="utf-8", newline="\n")
        print(f"작성: migrations/{fname}")

    # 레거시(=정적 baseline 이 아닌 모든 migrations/*.sql)를 archive/ 로 이동.
    ARCHIVE_DIR.mkdir(exist_ok=True)
    moved = 0
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if p.name in STATIC_BASELINE:
            continue
        p.rename(ARCHIVE_DIR / p.name)
        moved += 1
    print(f"레거시 마이그 {moved}개 → migrations/archive/ 이동.")
    print("\n다음: 양 DB 리셋 후 `migrate.py apply --target collection --seeds` / "
          "`--target backend --seeds`. 검증은 db-2-instance-bootstrap.md.")


if __name__ == "__main__":
    main()
