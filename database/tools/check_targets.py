"""마이그레이션/시드 타깃 태그 가드 (2-인스턴스 분리).

모든 database/migrations/*.sql 와 database/seeds/*.sql 이 명시적 ``-- target:`` 헤더를
선언하는지 검사한다. 미선언/오타가 있으면 exit 1. CI 에 걸어 2-DB 분리 후 백엔드 마이그가
실수로 수집 DB(미태그=collection 기본값)로 새는 회귀를 막는다.

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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check_dir(directory: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        head = path.read_text(encoding="utf-8")[:2000]
        m = _TARGET_RE.search(head)
        if not m:
            problems.append(f"[no target]  {path.name} — '-- target: <collection|backend|all>' 헤더 없음")
        elif m.group(1) not in VALID:
            problems.append(f"[bad target] {path.name} — '{m.group(1)}' (허용: {', '.join(VALID)})")
    return problems


def main() -> None:
    problems = check_dir(MIGRATIONS_DIR) + check_dir(SEEDS_DIR)
    if problems:
        print(f"타깃 태그 문제 {len(problems)}건:\n")
        for p in problems:
            print(f"  {p}")
        print("\n모든 마이그/시드는 '-- target:' 을 명시해야 합니다(미선언 시 collection 으로 오분류).")
        sys.exit(1)
    print("타깃 태그 OK — 모든 마이그/시드가 -- target: 을 명시합니다.")


if __name__ == "__main__":
    main()
