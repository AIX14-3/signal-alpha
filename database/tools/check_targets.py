"""마이그레이션/시드 타깃 태그 + target↔테이블 정합 가드 (2-인스턴스 분리).

두 가지를 검사한다(둘 중 하나라도 위반 시 exit 1, CI 게이트 권장):

1) **타깃 태그**: 모든 database/migrations/*.sql 와 database/seeds/*.sql 이 명시적
   ``-- target:`` 헤더(collection|backend|all)를 선언하는지. 미선언/오타가 있으면 실패.
   (미태그 기본값이 collection 이라, 백엔드 마이그가 실수로 수집 DB 로 새는 회귀를 막는다.)

2) **target↔테이블 정합**(마이그 전용): 각 마이그가 CREATE/ALTER 하는 테이블과 FK 참조
   테이블을 ``db_partition.py`` 버킷(BACKEND / PUBLISHED / 그 외=COLLECTION)으로 대조해
   다음을 강제한다 —
   - 대상 테이블은 그 마이그가 적용되는 **모든 DB 에 존재**해야 한다:
       target=collection → 대상 ∈ COLLECTION∪PUBLISHED,
       target=backend    → 대상 ∈ BACKEND∪PUBLISHED,
       target=all        → 대상 ∈ PUBLISHED (양쪽 DB 에 동일 적용되므로).
   - FK 는 **cross-DB 일 수 없다**: 소유 테이블이 사는 DB 집합 ⊆ 참조 테이블이 사는 DB 집합.
       (예: analysis_requests[COLLECTION] → users[BACKEND] 는 위반 → 2-DB 에서 적용 실패.)
   이 검사가 #531 2-DB 분리에서 "target 과 실제 대상 DB 가 어긋나는" 회귀(백엔드 마이그가
   수집 전용 테이블을 건드리는 류)를 사전에 잡는다.

archive/ 하위(레거시)는 검사하지 않는다(러너가 비재귀 glob 으로 무시).

사용법:
    python database/tools/check_targets.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = DB_DIR / "migrations"
SEEDS_DIR = DB_DIR / "seeds"
VALID = ("collection", "backend", "all")
_TARGET_RE = re.compile(r"^--\s*target:\s*(\w+)\s*$", re.MULTILINE)

# db_partition 을 단일 출처로 테이블 버킷을 가져온다.
sys.path.insert(0, str(DB_DIR))
import db_partition  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 각 버킷이 존재하는 물리 DB 집합(2-인스턴스).
_BUCKET_DBS = {
    "collection": frozenset({"coll"}),
    "backend": frozenset({"be"}),
    "published": frozenset({"coll", "be"}),
}
# 각 target 이 적용되는 DB 집합.
_TARGET_DBS = {
    "collection": frozenset({"coll"}),
    "backend": frozenset({"be"}),
    "all": frozenset({"coll", "be"}),
}

# 스키마 한정자(public. 또는 임의 스키마.)는 건너뛰고 식별자만 캡처.
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:\w+\.)?\"?(\w+)\"?",
    re.IGNORECASE,
)
_ALTER_TABLE_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:\w+\.)?\"?(\w+)\"?",
    re.IGNORECASE,
)
_REFERENCES_RE = re.compile(r"\bREFERENCES\s+(?:\w+\.)?\"?(\w+)\"?", re.IGNORECASE)
# 라인주석 / 블록주석 / 단일인용 문자열('' 이스케이프 포함)을 한 정규식 교대로 토큰화.
# 상호 배타가 핵심: 현재 위치에서 셋 중 하나만 매칭되므로 문자열 안의 --·/*·; 는 문자열에
# 흡수되고, 주석 안의 ' 는 주석에 흡수된다 → "무엇을 먼저 strip 하나"에서 생기는 상호오염을 원천 차단.
_NOISE_RE = re.compile(
    r"(?P<line>--[^\n]*)"
    r"|(?P<block>/\*.*?\*/)"
    r"|(?P<str>'(?:[^']|'')*')",
    re.DOTALL,
)


def _strip_noise(sql: str) -> str:
    """주석과 문자열 리터럴을 단일 패스로 동시에 제거(테이블/ FK 정규식의 오탐 방지).

    문자열은 ``''`` 로 비우고(내부 ;·--·/* 가 문장 분리/주석 제거를 깨지 않게), 주석은 공백으로.
    교대 정규식이라 주석 속 ``'``(예: 영어 소유격)나 문자열 속 ``--`` 가 서로를 오염시키지 않는다.
    dollar-quoted 본문(``$$ ... $$``)은 보존한다 — ``DO $$ ... CREATE TABLE ... $$`` 같은 멱등
    패턴 안의 테이블/FK 도 검사 대상이기 때문(함수 본문이 키워드를 포함해 드물게 오탐하면,
    조용한 누락보다 시끄러운 실패가 가드로서 안전하다).
    """
    return _NOISE_RE.sub(lambda m: "''" if m.lastgroup == "str" else " ", sql)


def bucket_of(table: str) -> str:
    # RETIRED_BACKEND_TABLES: 드롭됐지만 과거 마이그가 CREATE/ALTER 하던 backend 테이블.
    # 라이브엔 없어 BACKEND_TABLES 엔 없지만, 과거 마이그 locality 재검증엔 원 분류가 필요.
    retired = getattr(db_partition, "RETIRED_BACKEND_TABLES", frozenset())
    if table in db_partition.BACKEND_TABLES or table in retired:
        return "backend"
    if table in db_partition.PUBLISHED_TABLES:
        return "published"
    return "collection"


def parse_target(path: Path) -> str | None:
    head = path.read_text(encoding="utf-8")[:2000]
    m = _TARGET_RE.search(head)
    return m.group(1) if m else None


def analyze_sql(sql: str) -> tuple[set[str], list[tuple[str, str]]]:
    """(대상 테이블 집합, FK 엣지[(소유, 참조)]) 반환.

    문장 단위(;)로 끊어 CREATE/ALTER TABLE 의 소유 테이블을 정하고, 같은 문장 안의
    REFERENCES 를 그 소유 테이블의 FK 참조로 귀속한다.
    """
    clean = _strip_noise(sql)
    subjects: set[str] = set()
    edges: list[tuple[str, str]] = []
    for stmt in clean.split(";"):
        m = _CREATE_TABLE_RE.search(stmt) or _ALTER_TABLE_RE.search(stmt)
        if not m:
            continue
        owner = m.group(1).lower()
        subjects.add(owner)
        for ref_m in _REFERENCES_RE.finditer(stmt):
            edges.append((owner, ref_m.group(1).lower()))
    return subjects, edges


def check_tags(directory: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        target = parse_target(path)
        if target is None:
            problems.append(
                f"[no target]  {path.name} — '-- target: <collection|backend|all>' 헤더 없음"
            )
        elif target not in VALID:
            problems.append(f"[bad target] {path.name} — '{target}' (허용: {', '.join(VALID)})")
    return problems


def check_migration_locality(directory: Path) -> list[str]:
    """target↔테이블 정합 + cross-DB FK 검사(마이그 전용)."""
    problems: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        target = parse_target(path)
        if target not in _TARGET_DBS:
            continue  # 태그 검사에서 이미 보고됨
        target_dbs = _TARGET_DBS[target]
        subjects, edges = analyze_sql(path.read_text(encoding="utf-8"))

        for table in sorted(subjects):
            b = bucket_of(table)
            if not target_dbs <= _BUCKET_DBS[b]:
                problems.append(
                    f"[target mismatch] {path.name} — '{table}'({b}) 는 target={target} 의 "
                    f"적용 DB 에 존재하지 않습니다(적용 시 'relation does not exist' 실패)."
                )
        for owner, ref in edges:
            ob, rb = bucket_of(owner), bucket_of(ref)
            if not _BUCKET_DBS[ob] <= _BUCKET_DBS[rb]:
                problems.append(
                    f"[cross-DB FK]   {path.name} — '{owner}'({ob}) → '{ref}'({rb}) 는 "
                    "물리 분리 DB 간 FK 라 불가합니다(앱레벨 publisher 로 정합성 유지)."
                )
    return problems


def main() -> None:
    tag_problems = check_tags(MIGRATIONS_DIR) + check_tags(SEEDS_DIR)
    locality_problems = check_migration_locality(MIGRATIONS_DIR)
    problems = tag_problems + locality_problems
    if problems:
        print(f"타깃/정합 문제 {len(problems)}건:\n")
        for p in problems:
            print(f"  {p}")
        print(
            "\n모든 마이그/시드는 '-- target:' 을 명시하고, 대상 테이블·FK 가 그 DB 에 "
            "공존해야 합니다(db_partition.py 분류 기준)."
        )
        sys.exit(1)
    print("타깃/정합 OK — 태그 명시 + target↔테이블·FK 정합 통과.")


if __name__ == "__main__":
    main()
