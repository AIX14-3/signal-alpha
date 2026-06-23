"""SQLAlchemy 모델 — Alembic autogenerate 의 스키마 단일 진실원천.

**새 모델을 만들면 반드시 여기서 import** 해야 한다. ``database/alembic/env.py`` 는
``Base.metadata`` 만 보고 마이그레이션을 생성하므로, import 되지 않은 모델은 autogenerate
대상에서 누락된다(env.py 의 ``include_name`` 도 ``Base.metadata.tables`` 에 있는 테이블만 관리).
"""

from __future__ import annotations

from signal_alpha_data_access.models.base import Base
from signal_alpha_data_access.models.ml import MetaSignal, MlInference

__all__ = ["Base", "MlInference", "MetaSignal"]
