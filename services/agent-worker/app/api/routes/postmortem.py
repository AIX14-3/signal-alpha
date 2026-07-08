"""매매 부검 내러티브 내부 API — main-server 가 온디맨드로 호출한다.

부검 계산·3분류는 main-server(app/postmortem)가 하고, 이 엔드포인트는 그 **구조화 결과**를 받아
LLM 복기 서술만 생성한다(수치·판정 불변). LLM 미구성/권유표현/파싱 실패는 모두 `narrative: null` 로
graceful 하게 응답한다 — 부검 자체는 내러티브 없이도 성립하므로 호출측을 실패시키지 않는다.

게이팅: `POSTMORTEM_LLM_MODEL` env 가 없으면 비활성(null). provider 기본 gemini.
/internal/* 는 main.py 미들웨어가 X-Internal-Token 으로 보호한다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.narrate.base import NarrateError, narrate_client_from_env
from app.narrate.postmortem import PostmortemNarrator

router = APIRouter(prefix="/internal/postmortem", tags=["postmortem"])
logger = logging.getLogger(__name__)


class PostmortemNarrateRequest(BaseModel):
    scope: str  # 'trade' | 'patterns'
    stock: dict[str, Any] | None = None
    has_plan: bool | None = None
    round_trips: list[dict[str, Any]] = Field(default_factory=list)
    patterns: dict[str, Any] | None = None


@router.post("/narrate")
async def narrate_postmortem(
    payload: PostmortemNarrateRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    # POSTMORTEM_USE_LLM + POSTMORTEM_LLM_MODEL 로 게이팅(다른 narrate 소스와 동일 규약).
    built = narrate_client_from_env("POSTMORTEM", settings=settings)
    if built is None:
        return {"narrative": None}
    client, model, timeout = built
    narrator = PostmortemNarrator(client=client, model=model, timeout_seconds=timeout)
    try:
        narrative = await narrator.narrate(payload=payload.model_dump())
    except Exception as exc:  # noqa: BLE001
        # 이 엔드포인트의 계약은 "절대 실패시키지 않고 null 로 강등". NarrateError(빈 입력·권유·파싱)뿐
        # 아니라 LLM 클라이언트 에러(DartLlmAnalysisError=타임아웃/HTTP/응답없음)도 여기서 흡수한다.
        level = logging.INFO if isinstance(exc, NarrateError) else logging.WARNING
        logger.log(level, "postmortem narrate skipped: %s", exc)
        return {"narrative": None}
    return {
        "narrative": {
            "summary": narrative.summary,
            "key_facts": narrative.key_facts,
            "model": narrative.model,
        }
    }
