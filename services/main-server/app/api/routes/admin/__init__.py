"""어드민 API 라우터 — 도메인별 서브라우터(auth/users/billing/queue_ops/schedules) 조립.

기존 `from app.api.routes.admin import admin_router` 경로와 URL(prefix=/api/admin,
tags=["admin"])을 그대로 유지한다. 부모 라우터가 prefix·tags 를 부여하므로 서브라우터는
prefix 없이 전체 하위 경로만 선언한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.admin import auth, billing, queue_ops, schedules, users

# 테스트 호환: dependency_overrides 키와 직접 import 되는 헬퍼를 패키지 네임스페이스에 노출.
from app.api.routes.admin._serializers import (  # noqa: F401
    _schedule_health,
    _schedule_row,
    _validate_active_window,
    _validate_targets,
)
from app.api.routes.admin_auth import get_current_admin  # noqa: F401
from app.core.config import get_settings  # noqa: F401
from app.core.database import get_database_pool  # noqa: F401

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_router.include_router(auth.router)
admin_router.include_router(users.router)
admin_router.include_router(billing.router)
admin_router.include_router(queue_ops.router)
admin_router.include_router(schedules.router)

__all__ = ["admin_router"]
