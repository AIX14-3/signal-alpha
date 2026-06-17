"""Pure job-function classification and sector-demand aggregation (C4).

Each ``hiring_raw_details`` row is one posting (``job_count`` 1) whose ``keyword``
is the job title. ``classify_job_function`` maps that title to a coarse function
(ENGINEER, SALES…) by Korean/English keyword rules — deterministic, LLM-free.

``aggregate_sector_demand`` turns peer companies' postings into a forward signal:
for the functions a target stock depends on, it measures whether sector-wide
demand for those functions is rising or falling (recent vs prior window), blended
by the stock's exposure weights. The target stock is excluded so this is purely a
peer/sector signal — the stock's own momentum is the analyzer's separate component.

No DB, no clock: the loader supplies rows + ``as_of`` and the weights.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.analyzers.hiring.indicators import _parse_date  # shared date parser

# Ordered rules: first matching function wins. Keys are matched case-insensitively
# as substrings of the job title. Korean first (job titles are mostly Korean).
#
# Order matters: more specific functions precede the broad ENGINEER catch-all so an
# AI/data title is not swallowed by ENGINEER. DATA_AI therefore comes BEFORE ENGINEER
# (it owns the ai/ml/데이터 needles that used to sit in ENGINEER). ENGINEER is split
# into two tiers around RESEARCH: an explicit-role tier (엔지니어/engineer/developer/
# 프로그래머) BEFORE RESEARCH so "엔지니어" intent wins, and an activity tier (개발/
# 소프트웨어/클라우드…) AFTER RESEARCH so a research ROLE ("신약 개발 연구원") is not
# hijacked by the bare 개발 needle. The activity tier still precedes MANUFACTURING so
# "생산기술 엔지니어" stays ENGINEER — see test_hiring_job_functions.
#
# TODO(taxonomy): this is an intentionally COARSE, curated set (~10 functions),
# not a full directory of every market job. It is tuned to the sectors the tracked
# stocks span today (반도체/AI/플랫폼/자동차/바이오/엔터/경영지원). When the tracked
# universe expands into NEW sectors, round it out toward the standard top-level
# 직군 set — likely 금융/핀테크, 의료/헬스케어, 물류/유통, 게임, 교육, 서비스/운영.
# Driver: add a bucket only when real postings show a meaningful cluster falling to
# None or mis-bucketing (evidence-based, not speculative). Each new function needs
# all three: a needle rule here + a hiring_job_functions row + a
# hiring_job_function_stocks mapping (an unmapped function produces no signal).
# Game disambiguation (2-tier idiom, same shape as the ENGINEER split above).
#
# "game"/"게임" alone must NOT force DATA_AI. Two traps motivate this:
#   1. A genuine game-AI/ML role ("게임 AI 엔지니어", "ML for NPC/matchmaking") IS data/AI
#      work and belongs in DATA_AI.
#   2. A plain game-dev role ("게임 클라이언트 개발자", "Game Maintenance Engineer") is just
#      ENGINEER — but the very short DATA_AI needle "ai" substring-matches incidental words
#      like m-AI-ntenance / ret-AI-l / det-AI-l, wrongly pulling those into DATA_AI.
#
# So before the generic rules run we special-case game titles: if a game title carries a
# genuine AI/ML token it is DATA_AI, otherwise it stays ENGINEER and is shielded from the
# bare "ai"/"ml" substring needles. Non-game titles are untouched (fall through to the
# generic rules below, where "ai"/"ml" still own real AI/ML titles).
_GAME_NEEDLES: tuple[str, ...] = ("게임", "game")
# Genuine AI/ML signal *as a token*, not a stray substring. Word-boundary-ish: the needle
# must sit between non-letters (or string edges) so "maintenance"/"retail" do not count.
_GAME_AI_TOKENS: tuple[str, ...] = (
    "ai", "ml", "머신러닝", "딥러닝", "인공지능", "사이언티스트", "data scientist",
    "nlp", "npc", "매치메이킹", "matchmaking", "강화학습", "reinforcement",
)

DEFAULT_FUNCTION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DATA_AI", ("ai", "ml", "머신러닝", "딥러닝", "인공지능", "데이터", "사이언티스트",
                 "data scientist", "nlp")),
    # ENGINEER (role): explicit engineer/developer titles win over RESEARCH so
    # "선행개발 엔지니어"/"연구 엔지니어" stay ENGINEER (the 엔지니어 intent dominates).
    ("ENGINEER", ("엔지니어", "engineer", "developer", "프로그래머")),
    # RESEARCH before the ENGINEER activity tier: a research ROLE must not be hijacked
    # by the bare 개발 needle — "신약 개발 연구원" → RESEARCH (연구원), not ENGINEER.
    ("RESEARCH", ("연구", "r&d", "연구원", "선행", "박사", "research", "임상", "신약", "과학자")),
    # ENGINEER (activity): generic engineering needles, checked after RESEARCH.
    ("ENGINEER", ("개발", "소프트웨어", "백엔드", "프론트", "서버", "sw", "클라우드",
                  "아키텍트", "architect", "인프라", "devops", "sre")),
    ("MANUFACTURING", ("생산", "제조", "공정", "품질", "설비", "양산", "manufactur", "공장", "장비")),
    ("CREATIVE", ("프로듀서", "producer", "a&r", "repertoire", "아티스트", "작곡", "작사",
                  "드라마", "음악", "연출", "엔터")),
    ("SALES", ("영업", "세일즈", "sales", "판매", "리테일")),
    ("MARKETING", ("마케팅", "marketing", "브랜드", "홍보", "pr", "콘텐츠")),
    ("DESIGN", ("디자인", "design", "ux", "ui")),
    ("PLANNING", ("기획", "전략", "pm", "프로덕트", "사업개발", "planning")),
    ("CORPORATE", ("인사", "재무", "회계", "경영지원", "법무", "hr", "총무", "구매")),
)

LABELS: dict[str, str] = {
    "DATA_AI": "데이터/AI",
    "ENGINEER": "개발/엔지니어",
    "RESEARCH": "연구개발(R&D)",
    "MANUFACTURING": "생산/제조",
    "CREATIVE": "콘텐츠/크리에이티브",
    "SALES": "영업",
    "MARKETING": "마케팅",
    "DESIGN": "디자인",
    "PLANNING": "기획/전략",
    "CORPORATE": "경영지원",
}


def _token_present(needle: str, text: str) -> bool:
    """True if ``needle`` appears in ``text`` bounded by non-letters / string edges.

    Guards the very short AI/ML tokens ("ai", "ml") from matching inside unrelated
    words (m-ai-ntenance, ret-ai-l, HT-ml). Korean has no casing/word breaks, so a
    Korean needle is treated as a plain substring (the ASCII-letter boundary check
    only constrains ASCII needles).
    """
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            return False
        before = text[idx - 1] if idx > 0 else ""
        after = text[idx + len(needle)] if idx + len(needle) < len(text) else ""
        if not (before.isascii() and before.isalpha()) and not (
            after.isascii() and after.isalpha()
        ):
            return True
        start = idx + 1


def _classify_game(text: str) -> str | None:
    """Disambiguate game titles: genuine AI/ML-in-games → DATA_AI, else ENGINEER.

    Returns None for non-game titles so the caller falls through to the generic rules.
    """
    if not any(needle in text for needle in _GAME_NEEDLES):
        return None
    if any(_token_present(token, text) for token in _GAME_AI_TOKENS):
        return "DATA_AI"
    return "ENGINEER"


def classify_job_function(
    keyword: str | None,
    *,
    rules: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_FUNCTION_RULES,
) -> str | None:
    """Map a job title to a function key, or None when nothing matches."""
    if not keyword:
        return None
    text = keyword.casefold()
    # Game disambiguation runs first so "game" alone never forces DATA_AI (see _GAME_*).
    # Only applies when the default taxonomy is in use; a custom ``rules`` set opts out.
    if rules is DEFAULT_FUNCTION_RULES:
        game = _classify_game(text)
        if game is not None:
            return game
    for function_key, needles in rules:
        if any(needle in text for needle in needles):
            return function_key
    return None


@dataclass(frozen=True)
class FunctionDemand:
    function_key: str
    weight: float
    recent_count: float
    prior_count: float
    momentum_pct: float | None  # (recent - prior) / prior across peers; None if prior 0


@dataclass(frozen=True)
class SectorDemand:
    momentum_pct: float | None  # weight-weighted mean of per-function momentum
    coverage_weight: float      # summed weight of functions that had a computable momentum
    functions: list[FunctionDemand] = field(default_factory=list)

    def as_metadata(self) -> dict:
        return {
            "momentum_pct": self.momentum_pct,
            "coverage_weight": self.coverage_weight,
            "functions": [
                {
                    "function_key": f.function_key,
                    "weight": f.weight,
                    "momentum_pct": f.momentum_pct,
                    "recent_count": f.recent_count,
                    "prior_count": f.prior_count,
                }
                for f in self.functions
            ],
        }


def aggregate_sector_demand(
    rows: list[dict],
    *,
    as_of: date,
    lookback_days: int,
    function_weights: dict[str, float],
    target_stock_id: int | None = None,
    classify=classify_job_function,
) -> SectorDemand:
    """Peer demand momentum for the functions a stock depends on.

    ``rows`` are cross-company postings (each with stock_id, keyword, job_count,
    observed_date). Rows from ``target_stock_id`` are excluded so the signal is
    purely peer/sector. Returns an all-None SectorDemand when there is no usable
    peer data, which makes the analyzer fall back to own-momentum only.
    """
    if not function_weights:
        return SectorDemand(momentum_pct=None, coverage_weight=0.0)

    midpoint = as_of - timedelta(days=max(1, lookback_days) // 2)
    recent: dict[str, float] = {}
    prior: dict[str, float] = {}
    for row in rows:
        if target_stock_id is not None and row.get("stock_id") == target_stock_id:
            continue
        function_key = classify(row.get("keyword"))
        if function_key is None or function_key not in function_weights:
            continue
        observed = _parse_date(row.get("observed_date"))
        if observed is None:
            continue
        count = float(row.get("job_count") or 0)
        bucket = recent if observed > midpoint else prior
        bucket[function_key] = bucket.get(function_key, 0.0) + count

    functions: list[FunctionDemand] = []
    weighted_sum = 0.0
    coverage = 0.0
    for function_key, weight in function_weights.items():
        recent_count = recent.get(function_key, 0.0)
        prior_count = prior.get(function_key, 0.0)
        momentum = (recent_count - prior_count) / prior_count if prior_count > 0 else None
        functions.append(
            FunctionDemand(
                function_key=function_key,
                weight=weight,
                recent_count=recent_count,
                prior_count=prior_count,
                momentum_pct=momentum,
            )
        )
        if momentum is not None and weight > 0:
            weighted_sum += momentum * weight
            coverage += weight

    momentum_pct = weighted_sum / coverage if coverage > 0 else None
    return SectorDemand(
        momentum_pct=momentum_pct,
        coverage_weight=coverage,
        functions=functions,
    )


