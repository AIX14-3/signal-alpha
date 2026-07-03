"""판정 → 차단 결정. guard_site_status 는 mode 별로만 갱신한다.

- advisory: 제안(guard_recommendations)만 적재 — 사람이 승인해야 차단.
- auto: GUARD_AUTO_MAX_SCOPE 상한·쿨다운 안에서만 자동 차단/해제.
  whole_site 는 auto 라도 자동 실행 금지(제안으로 강등, 사람 승인 필수).
- manual: 에이전트는 관여하지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.guard.judge import GeoRiskJudgment

logger = logging.getLogger("guard_gate")

AGENT_ACTOR = "agent:geo-risk-monitor"
SCOPE_ORDER: tuple[str, ...] = ("report_generation", "report_view", "whole_site")


def severity_to_scope(severity: int) -> str | None:
    if severity >= 90:
        return "whole_site"
    if severity >= 70:
        return "report_view"
    if severity >= 50:
        return "report_generation"
    return None


async def apply_judgment(
    connection: Any,
    settings: Any,
    judgment: GeoRiskJudgment,
    *,
    news_event_id: int | None,
) -> dict[str, Any]:
    row = await connection.fetchrow(
        "SELECT status, scope, mode, triggered_by, updated_at FROM guard_site_status WHERE id = 1"
    )
    if row is None:
        logger.warning("guard_site_status singleton row missing; skipping gate decision")
        return {"action": "no_status_row"}

    mode = row["mode"]
    risky = (
        judgment.is_geopolitical_risk
        and judgment.severity >= int(settings.guard_severity_threshold)
    )

    if not risky:
        # 자동 해제 — auto 모드 + 에이전트가 건 차단 + 완화 방향 + 쿨다운(최소 유지시간) 경과.
        if (
            mode == "auto"
            and row["status"] == "blocked"
            and row["triggered_by"] == AGENT_ACTOR
            and judgment.direction == "deescalation"
            and _cooldown_passed(row["updated_at"], settings)
        ):
            await connection.execute(
                "UPDATE guard_site_status"
                " SET status = 'ok', reason = NULL, resume_at = NULL,"
                "     triggered_by = $1, updated_at = now()"
                " WHERE id = 1",
                AGENT_ACTOR,
            )
            await _record_audit(
                connection, action="unblock", scope=row["scope"], reason=judgment.summary
            )
            return {"action": "auto_released"}
        return {"action": "none"}

    scope = severity_to_scope(judgment.severity) or "report_generation"

    if mode == "manual":
        return {"action": "ignored_manual", "scope": scope}

    # advisory 전부 + auto 의 whole_site(자동 실행 금지)는 제안으로 처리.
    if mode == "advisory" or scope == "whole_site":
        return await _recommend(connection, judgment, scope=scope, news_event_id=news_event_id)

    # auto — scope 상한 적용 후 즉시 차단.
    capped_scope = _cap_scope(scope, settings.guard_auto_max_scope)
    if row["status"] == "blocked" and _scope_rank(row["scope"]) >= _scope_rank(capped_scope):
        return {"action": "already_blocked", "scope": row["scope"]}
    if row["triggered_by"] == AGENT_ACTOR and not _cooldown_passed(row["updated_at"], settings):
        # 쿨다운 중이라 자동 차단을 못 걸더라도 신호를 삼키지 않는다 — 관리자가 즉시
        # 승인·상향할 수 있도록 제안으로 남긴다(급격한 확전이 최대 쿨다운 동안 유실 방지).
        result = await _recommend(connection, judgment, scope=capped_scope, news_event_id=news_event_id)
        return {**result, "auto_deferred": "cooldown"}
    await connection.execute(
        "UPDATE guard_site_status"
        " SET status = 'blocked', scope = $1, reason = $2, triggered_by = $3, updated_at = now()"
        " WHERE id = 1",
        capped_scope,
        judgment.summary,
        AGENT_ACTOR,
    )
    await _record_audit(connection, action="block", scope=capped_scope, reason=judgment.summary)
    return {"action": "auto_blocked", "scope": capped_scope}


async def _recommend(
    connection: Any,
    judgment: GeoRiskJudgment,
    *,
    scope: str,
    news_event_id: int | None,
) -> dict[str, Any]:
    # 같은 scope 의 pending 제안이 이미 있으면 카드를 새로 쌓지 않되(도배 방지),
    # 최신 판정으로 갱신한다 — 그러지 않으면 관리자는 며칠 전 사건의 사유·근거가 박제된
    # 카드를 보고, 정작 지금 벌어지는 사건(같은 scope 대역)을 놓친다.
    updated = await connection.fetchval(
        "UPDATE guard_recommendations"
        " SET news_event_id = $2, severity = $3, reason = $4, created_at = now()"
        " WHERE id = ("
        "   SELECT id FROM guard_recommendations"
        "   WHERE status = 'pending' AND suggested_scope = $1"
        "   ORDER BY created_at DESC LIMIT 1"
        " )"
        " RETURNING id",
        scope,
        news_event_id,
        judgment.severity,
        judgment.summary,
    )
    if updated is not None:
        return {"action": "recommendation_refreshed", "scope": scope}
    await connection.execute(
        "INSERT INTO guard_recommendations (news_event_id, suggested_scope, severity, reason)"
        " VALUES ($1, $2, $3, $4)",
        news_event_id,
        scope,
        judgment.severity,
        judgment.summary,
    )
    return {"action": "recommended", "scope": scope}


async def _record_audit(connection: Any, *, action: str, scope: str | None, reason: str | None) -> None:
    await connection.execute(
        "INSERT INTO guard_status_audit (action, scope, reason, actor) VALUES ($1, $2, $3, $4)",
        action,
        scope,
        reason,
        AGENT_ACTOR,
    )


def _scope_rank(scope: Any) -> int:
    try:
        return SCOPE_ORDER.index(str(scope))
    except ValueError:
        return -1


def _cap_scope(scope: str, max_scope: str) -> str:
    if _scope_rank(max_scope) < 0:
        max_scope = "report_generation"
    return scope if _scope_rank(scope) <= _scope_rank(max_scope) else max_scope


def _cooldown_passed(updated_at: Any, settings: Any) -> bool:
    if not isinstance(updated_at, datetime):
        return True
    reference = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - reference).total_seconds()
    return elapsed >= float(settings.guard_auto_cooldown_sec)
