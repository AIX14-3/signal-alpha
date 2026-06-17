"""Discord 웹훅 알림 — collector_runs 통계를 구조화 Embed 로 발송 (Phase 5).

수집 run 의 성공률/실패 건수를 Discord 전용 채널(#hiring-crawler-log)에 Embed 로
찍어 운영 가시성을 제공한다. 인증 부담 없는 웹훅 POST(`{"embeds": [...]}`)만 사용.

설계 원칙:
  - webhook_url 빈 값 → no-op(False). 미설정 환경에서 데몬이 안전하게 돈다.
  - 발송 예외는 삼켜 로깅 → 알림 실패가 self-healing 데몬을 죽이지 않게.
  - Embed 빌더는 순수 함수 → 포맷 단위 테스트 가능.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.observability.stats import RunStats, _pct

logger = logging.getLogger(__name__)

# 상태/사유별 Embed 좌측 색상 막대(발표 장표에서 한눈에 심각도 구분).
_COLOR_FAILED = 0xE74C3C   # 적 — 전건 실패
_COLOR_PARTIAL = 0xE67E22  # 주 — 거부율 초과(부분 실패)
_COLOR_SILENT = 0xF1C40F   # 황 — 침묵 실패(신규 0건)
_COLOR_DEFAULT = 0x95A5A6  # 회 — 기타


def _embed_color(status: str, reason: str) -> int:
    if "침묵" in reason:
        return _COLOR_SILENT
    if status == "failed":
        return _COLOR_FAILED
    if status == "partial":
        return _COLOR_PARTIAL
    return _COLOR_DEFAULT


def build_run_alert_embed(
    collector_type: str,
    stats: RunStats,
    status: str,
    *,
    run_id: int,
    reason: str,
) -> dict[str, Any]:
    """수집 run 경보용 Discord Embed dict 생성(순수 함수).

    수집/신규/중복·스킵/실패 건수 + 적재성공률/실패율 + 판정 사유를 필드로 구조화한다.
    """
    return {
        "title": f"🚨 {collector_type} 수집 경보",
        "description": f"**사유:** {reason}",
        "color": _embed_color(status, reason),
        "fields": [
            {"name": "상태", "value": status, "inline": True},
            {"name": "run_id", "value": str(run_id), "inline": True},
            {"name": "​", "value": "​", "inline": True},  # 3열 정렬용 여백
            {"name": "수집", "value": str(stats.collected), "inline": True},
            {"name": "신규", "value": str(stats.inserted), "inline": True},
            {"name": "중복/스킵", "value": str(stats.skipped), "inline": True},
            {"name": "실패", "value": str(stats.failed), "inline": True},
            {"name": "적재성공률", "value": f"{_pct(stats.ingest_success_rate)}%", "inline": True},
            {"name": "실패율", "value": f"{_pct(stats.failure_rate)}%", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def send_discord_alert(
    http: httpx.AsyncClient,
    webhook_url: str,
    embed: dict[str, Any],
) -> bool:
    """Discord 웹훅으로 Embed 1건 발송. 성공 True / no-op·실패 False.

    webhook_url 이 비어 있으면 발송하지 않고 False(미설정 환경 안전 동작).
    네트워크/HTTP 예외는 삼켜 로깅한다 — 알림 실패가 데몬을 중단시키지 않게.
    """
    if not webhook_url:
        logger.debug("DISCORD_WEBHOOK_URL 미설정 — 알림 skip")
        return False
    try:
        response = await http.post(webhook_url, json={"embeds": [embed]})
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - 알림 실패는 데몬을 죽이지 않는다
        logger.warning("Discord 알림 발송 실패: %s", exc)
        return False
