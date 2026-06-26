"""[DEPRECATED] 백엔드 DB 그린필드 부트스트랩 — 타깃별 baseline 재정리로 은퇴됨.

이전에는 "전체 마이그 적용 → 수집 전용 테이블 DROP CASCADE" 트림 방식으로 백엔드 DB 를
부트스트랩했다. 마이그레이션이 `-- target:` 으로 깔끔히 분리된 지금은 트림이 불필요하다:

    # 백엔드 DB (백엔드+발행+인프라만, collection 마이그는 적용 안 됨)
    uv run python database/migrate.py apply --target backend --seeds

    # 수집 DB (수집+발행+인프라)
    uv run python database/migrate.py apply --target collection --seeds

표 baseline(0002/0003/0004)을 (재)생성하려면 database/rebaseline.py 를 쓴다.
절차: docs/runbooks/db-2-instance-bootstrap.md.
"""

from __future__ import annotations

import sys

_MSG = __doc__


def main() -> None:
    sys.stderr.write(
        "split_schema.py 는 은퇴되었습니다(타깃별 baseline 재정리).\n"
        "백엔드 DB: `python database/migrate.py apply --target backend --seeds` 를 쓰세요.\n"
        "자세히: docs/runbooks/db-2-instance-bootstrap.md\n"
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
