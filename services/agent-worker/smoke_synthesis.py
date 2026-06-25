"""끝단 종합 LLM 한 방 스모크 — '키를 넣으면 진짜 동작하는지' 확인용.

전체 파이프라인을 돌리지 않고, .env 의 SYNTHESIS_* + GEMINI/OPENAI 키만으로 종합기를
구성해 한 번 호출한 뒤 결과(headline/narrative/...)를 출력한다.

사용:
  # .env 에 SYNTHESIS_USE_LLM=true, SYNTHESIS_LLM_MODEL=..., GEMINI_API_KEY=... 설정 후
  python services/agent-worker/smoke_synthesis.py

비활성(키/토글 미설정)이면 결정론 폴백 경로임을 알리고 비정상 종료한다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서도 한글/기호 출력이 깨지지 않도록 UTF-8 강제.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import get_settings
from app.synthesis.tasks import _build_synthesizer, synthesis_llm_enabled

# 발행 신호는 LLM 이 바꾸지 않는다(설명 필드만 생성) — 대표 입력.
_SAMPLE_CONTEXT = {
    "signal": "positive",
    "warning_level": "info",
    "is_published": True,
    "evidence": [
        {"source": "DART", "title": "분기 영업이익 전년 대비 개선", "weight": 0.6},
        {"source": "DATALAB", "title": "검색 관심도 상승 추세", "weight": 0.3},
    ],
    "risk": {"vetoed": False, "keywords": []},
}


async def _run() -> int:
    settings = get_settings()
    if not synthesis_llm_enabled(settings):
        print(
            "SYNTHESIS LLM 비활성 — 결정론 템플릿 폴백 경로입니다.\n"
            "  .env 에 SYNTHESIS_USE_LLM=true, SYNTHESIS_LLM_PROVIDER(gemini|openai),\n"
            "  SYNTHESIS_LLM_MODEL, 해당 provider 의 API 키를 설정하세요."
        )
        return 1

    synth = _build_synthesizer(settings)
    if synth is None:  # 방어적: enabled 가 True 면 보통 도달 안 함
        print("종합기 구성 실패(_build_synthesizer=None). provider/키 설정을 확인하세요.")
        return 1

    print(f"provider model={synth.model} client={type(synth._client).__name__} — 호출 중...")
    narrative = await synth.synthesize(_SAMPLE_CONTEXT)
    print("\n=== SYNTHESIS OK ===")
    print(f"headline      : {narrative.headline}")
    print(f"narrative     : {narrative.narrative}")
    print(f"key_points    : {narrative.key_points}")
    print(f"caution_points: {narrative.caution_points}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
