"""SQLAlchemy 선언적 베이스 — Alembic autogenerate 의 스키마 단일 진실원천.

이 패키지의 모델들이 ``Base.metadata`` 에 모이고, ``database/alembic/env.py`` 가 이를
``target_metadata`` 로 써서 모델 ↔ 실 DB 차이를 마이그레이션으로 자동 생성한다.

런타임 쿼리는 기존대로 asyncpg 원시 SQL 리포지토리(``repositories/``)를 쓴다 — 모델은 현재
**스키마 정의 + 마이그레이션 생성용**이다(점진 도입). 새 테이블/컬럼은 여기 모델로 추가·수정·
삭제하고 ``alembic revision --autogenerate`` 로 마이그레이션을 만든다.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

# 주의: 전역 naming_convention 은 지금 두지 않는다. 초기 모델들은 레거시 .sql(001~023)이
# 만든 **기존 테이블을 그대로 미러링**하므로, 제약/인덱스 이름을 모델에서 실제(Postgres 기본
# 이름 포함)와 똑같이 명시(name=...)해 첫 autogenerate 의 불필요한 rename diff 를 피한다.
# 스키마를 충분히 모델링한 뒤, 일괄 정규화 마이그레이션과 함께 convention 을 도입하는 편이 안전하다.


class Base(DeclarativeBase):
    pass
