"""Alembic 환경 — 모델 메타데이터 ↔ 실 DB 차이로 마이그레이션 생성/적용.

핵심 안전장치(점진 도입):
- ``target_metadata = Base.metadata`` — 모델로 정의한 테이블만 진실원천.
- ``include_name`` 가 **모델에 정의된 테이블만** autogenerate 대상으로 한정한다. 아직 모델이
  없는 레거시 테이블(stocks, raw_documents, …)을 Alembic 이 실수로 DROP 제안하지 못하게 막는다.
  → 새 테이블을 모델로 추가하면 자동으로 관리 범위에 들어온다.

DB 접속: DATABASE_URL(환경변수) > 루트 .env. (database/migrate.py 와 동일 규칙, 동기 psycopg2.)
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from signal_alpha_data_access.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

ROOT_DIR = Path(__file__).resolve().parents[2]


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = ROOT_DIR / ".env"
    if env.exists():
        for raw in env.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        "DATABASE_URL 을 찾을 수 없습니다 (환경변수 또는 루트 .env). "
        "예: DATABASE_URL=postgresql://user:pw@localhost:5432/signal_alpha"
    )


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """모델(=Base.metadata)에 정의된 테이블만 관리. 미모델링 레거시 테이블은 건드리지 않는다."""
    if type_ == "table":
        return name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
