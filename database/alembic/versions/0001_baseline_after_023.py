"""baseline: schema state after legacy SQL migrations 001~023 (database/migrations/)

레거시 SQL 마이그레이션 001~023 적용 후 상태. 하이브리드 도입의 시작점이다. 이 리비전은 스키마를 바꾸지 않는다(no-op) — 기존 23개 .sql 이
만든 스키마를 'Alembic 이 아는 시작 상태'로 표시할 뿐이다. 이후의 모든 추가/수정/삭제는
새 Alembic 리비전(down_revision 이 이 리비전을 가리킴)으로 쌓인다.

부트스트랩:
- 이미 023 까지 적용된 기존 DB:
    alembic stamp 0001_baseline_after_023
- 새 DB(처음부터):
    python database/migrate.py apply        # 레거시 .sql 로 001~023 스키마 생성
    alembic stamp 0001_baseline_after_023   # 베이스라인 표시
    alembic upgrade head                     # 이후 Alembic 리비전 적용
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "0001_baseline_after_023"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # no-op: 레거시 database/migrations/001~023.sql 이 이미 스키마를 만든다.
    # 이 베이스라인 이후의 변경부터 Alembic 이 관리한다.
    pass


def downgrade() -> None:
    # 베이스라인 이전(레거시 .sql 영역)으로는 내려가지 않는다.
    pass
