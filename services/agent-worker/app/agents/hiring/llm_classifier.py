"""LLM skill/duty-focus classifier for the Hiring Tier-B agent.

Given a stock's hiring skill/job-function focus (deterministically extracted from
the postings), the LLM writes a one-sentence *strategic* rationale — "what is this
company investing its hiring in?" — and a short focus label. It is descriptive
only: it never invents a buy/sell call, a return forecast, or a target price
(docs §9/§10), and it NEVER touches the rule analyzer's score/direction. Output is
a strict JSON contract validated here; on a malformed body the verdict degrades to
an empty rationale so the agent falls back to the deterministic focus summary.

Boundary: this is the agent/enrichment layer — it may call an LLM. The rule
analyzers never import it (analyzers stay deterministic). Mirrors the DataLab
``DataLabCauseClassifier``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.requery_focus import requery_focus_prompt_block

PROMPT_VERSION = "hiring-focus-v1"


@dataclass(frozen=True)
class FocusVerdict:
    focus: str | None
    rationale: str


class HiringSkillClassifier:
    """Wrap a JSON LLM client to summarize a stock's hiring skill/duty focus."""

    def __init__(self, client: Any, *, prompt_version: str = PROMPT_VERSION) -> None:
        self._client = client
        self.prompt_version = prompt_version

    @property
    def model(self) -> str:
        return getattr(self._client, "model", None) or "gemini"

    async def classify(
        self,
        *,
        stock_code: str,
        top_functions: list[str],
        top_skills: list[str],
        momentum_pct: float | None,
        requery_focus: str | None = None,
    ) -> FocusVerdict:
        """Return the LLM's focus verdict. Raises on transport/JSON failure so the
        caller's fallback path engages (deterministic focus summary).

        ``requery_focus`` (optional): the orchestrator's re-query hint — when set,
        a focused re-read instruction is appended to the prompt; ``None`` leaves
        the prompt byte-identical to a normal analyze."""
        payload = await self._client.generate_json(
            _build_prompt(
                stock_code=stock_code,
                top_functions=top_functions,
                top_skills=top_skills,
                momentum_pct=momentum_pct,
                requery_focus=requery_focus,
            )
        )
        return _parse_verdict(payload)


def _build_prompt(
    *,
    stock_code: str,
    top_functions: list[str],
    top_skills: list[str],
    momentum_pct: float | None,
    requery_focus: str | None = None,
) -> str:
    def _pct(value: float | None) -> str:
        return "데이터 없음" if value is None else f"{value * 100:+.1f}%"

    functions = ", ".join(top_functions) if top_functions else "식별된 직무 없음"
    skills = ", ".join(top_skills) if top_skills else "식별된 기술 없음"
    return (
        "너는 한국 기업의 채용 공고에서 드러난 '무엇을 위한 채용인가'를 요약하는 분석 보조다.\n"
        "매수/매도 추천, 수익 전망, 목표가는 절대 언급하지 말고, 어떤 직무·기술 역량에\n"
        "투자하고 있는지 흔적만 서술하라. 점수나 방향(상승/하락)도 판정하지 마라.\n\n"
        f"종목코드: {stock_code}\n"
        f"주요 채용 직무(빈도순): {functions}\n"
        f"주요 요구 기술(빈도순): {skills}\n"
        f"채용 공고 모멘텀(최근 vs 이전): {_pct(momentum_pct)}\n\n"
        f"{requery_focus_prompt_block(requery_focus)}"
        "다음 JSON만 출력하라(다른 텍스트 금지):\n"
        '{"focus": "핵심 채용 테마 3~6자(예: 데이터/AI 인재)", '
        '"rationale": "한국어 한 문장 근거(매수/매도·수익·목표가 표현 금지)"}'
    )


def _parse_verdict(payload: Any) -> FocusVerdict:
    if not isinstance(payload, dict):
        return FocusVerdict(focus=None, rationale="")
    focus = str(payload.get("focus") or "").strip() or None
    rationale = str(payload.get("rationale") or "").strip()
    return FocusVerdict(focus=focus, rationale=rationale)
