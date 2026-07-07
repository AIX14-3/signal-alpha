"""LLM market-regime classifier — evidence tag only, never a verdict.

Given a deterministic market/sector context (dispersion, macro proxies, a short
note), the LLM picks ONE regime label from a fixed enum and writes a one-line
Korean rationale. It must NOT emit a buy/sell call, a direction, or a score — the
prompt forbids it and ``_reject_investment_advice`` enforces it. On ANY malformed
/ out-of-enum / policy-violating / transport failure the tag degrades to
``label=None`` (deterministic degrade): the caller then simply omits the regime
evidence block, exactly like ``dart/evidence.py`` omits its block on failure.

Boundary: this is the agent/enrichment layer — it may call an LLM. Rule analyzers
and the deterministic feature path (``features.regime_features``) never import it
and never read its output back (numbers stay deterministic + PIT).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

PROMPT_VERSION = "regime-tag-v1"

# Regime taxonomy — a deliberately small, mutually-recognisable set. ``neutral``
# is the safe default; anything the LLM cannot map to the enum degrades to None.
REGIME_LABELS: frozenset[str] = frozenset(
    {
        "ai_capex_boom",   # AI/반도체 capex 주도 위험선호 (섹터 쏠림)
        "rate_tightening",  # 금리 상승/긴축 국면
        "credit_stress",    # 신용 스프레드 확대/유동성 경색
        "risk_on",          # 광범위 위험선호 (분산된 상승)
        "neutral",          # 뚜렷한 레짐 없음 (기본값)
    }
)

# Reuse the DART guard idea: a rationale containing buy/sell/target-price language
# is a policy violation → degrade to label=None (never surface investment advice).
_PROHIBITED_ADVICE = re.compile(
    r"(매수|매도|목표\s*주?가|비중\s*확대|비중\s*축소|사세요|파세요|buy|sell|target\s*price)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RegimeTag:
    """A market-regime EVIDENCE tag. Never carries a score/direction/verdict.

    ``label`` is one of ``REGIME_LABELS`` or ``None`` (deterministic degrade on any
    malformed/enum-miss/policy/transport failure). ``rationale`` is a one-line
    human note; ``confidence`` is display-only provenance (NOT an ML feature);
    ``model`` records LLM provenance; ``error`` is set on a degrade path.
    """

    label: str | None
    rationale: str = ""
    confidence: float = 0.0
    model: str | None = None
    prompt_ver: str = PROMPT_VERSION
    error: str | None = None


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN — comparisons are always False; clamp would pass it
        return 0.0
    return max(0.0, min(1.0, number))


def _parse_tag(payload: Any, *, model: str | None) -> RegimeTag:
    """Validate an LLM payload into a ``RegimeTag``; degrade to ``label=None``.

    An out-of-enum label, non-dict payload, or advice-bearing rationale all map to
    ``label=None`` — the caller omits the evidence block (never a fabricated tag).
    """
    if not isinstance(payload, Mapping):
        return RegimeTag(label=None, rationale="(LLM 응답 형식 오류)", model=model,
                         error="malformed: not a JSON object")
    rationale = str(payload.get("rationale") or "").strip() or "(근거 미제공)"
    if _PROHIBITED_ADVICE.search(rationale):
        # Policy guard (mirror _reject_investment_advice): advice → deterministic degrade.
        return RegimeTag(label=None, rationale="(투자조언 표현 감지 — 태그 폐기)",
                         model=model, error="policy: investment advice in rationale")
    raw = str(payload.get("regime") or payload.get("label") or "").strip().lower()
    if raw not in REGIME_LABELS:
        return RegimeTag(label=None, rationale=rationale, model=model,
                         error=f"enum-miss: {raw!r}")
    return RegimeTag(
        label=raw,
        rationale=rationale,
        confidence=_clamp_float(payload.get("confidence")),
        model=model,
    )


def _build_prompt(context: Mapping[str, Any]) -> str:
    """Build the regime-tagging prompt. Forbids buy/sell/target-price outright."""
    lines = "\n".join(f"- {k}: {v}" for k, v in context.items()) or "- (컨텍스트 없음)"
    labels = " | ".join(sorted(REGIME_LABELS))
    return (
        "너는 한국 주식시장의 '레짐(국면)'을 분류하는 분석 보조다.\n"
        "매수/매도 추천, 목표주가, 방향/점수 전망은 절대 하지 마라. 아래 결정론 지표를\n"
        "근거로 현재가 어떤 레짐에 가장 가까운지 하나의 라벨로만 태깅하라(태그일 뿐 판정 아님).\n\n"
        "레짐 정의:\n"
        "- ai_capex_boom : AI/반도체 capex 주도의 섹터 쏠림 위험선호.\n"
        "- rate_tightening: 금리 상승/긴축이 지배하는 국면.\n"
        "- credit_stress : 신용 스프레드 확대/유동성 경색.\n"
        "- risk_on       : 분산된 광범위 위험선호.\n"
        "- neutral       : 뚜렷한 레짐이 없음(기본값).\n\n"
        "결정론 지표(숫자는 이 값들만 사용, 새 숫자 생성 금지):\n"
        f"{lines}\n\n"
        "다음 JSON만 출력하라(다른 텍스트 금지):\n"
        f'{{"regime": "{labels}", '
        '"rationale": "한국어 한 문장 근거(매수/매도·목표주가 표현 금지)", '
        '"confidence": 0.0~1.0 사이 숫자}'
    )


async def classify_regime(context: Mapping[str, Any], *, client: Any) -> RegimeTag:
    """Classify the current market regime into a ``RegimeTag`` (evidence only).

    Builds a buy/sell-forbidding prompt, calls ``client.generate_json``, and
    validates to the enum. ANY exception (transport/JSON) or malformed/out-of-enum
    /policy response degrades to ``RegimeTag(label=None, ...)`` — never raises, so
    a caller can treat "no tag" identically to the LLM-off path.
    """
    model = getattr(client, "model", None)
    try:
        payload = await client.generate_json(_build_prompt(context))
    except Exception as exc:  # noqa: BLE001 — LLM failure is a deterministic degrade
        return RegimeTag(label=None, model=model,
                         error=f"{type(exc).__name__}: {exc}"[:300])
    return _parse_tag(payload, model=model)


class RegimeClassifier:
    """Thin wrapper over a JSON LLM client, mirroring ``DataLabCauseClassifier``."""

    def __init__(self, client: Any, *, prompt_version: str = PROMPT_VERSION) -> None:
        self._client = client
        self.prompt_version = prompt_version

    @property
    def model(self) -> str:
        return getattr(self._client, "model", None) or "gemini"

    async def classify(self, context: Mapping[str, Any]) -> RegimeTag:
        return await classify_regime(context, client=self._client)


def build_regime_tagger(settings: Any) -> RegimeClassifier | None:
    """Build the regime tagger, or ``None`` for a byte-identical OFF path.

    Returns ``None`` when ``regime_use_llm`` is off OR no Gemini key is configured
    — identical to the LLM-off behaviour (no tag emitted). Mirrors
    ``build_dart_evidence_extractor``: the LLM is wired only on an explicit opt-in.
    STUB: this factory is not injected into any production handler.
    """
    if not getattr(settings, "regime_use_llm", False):
        return None
    api_key = str(getattr(settings, "gemini_api_key", "") or "")
    if not api_key:
        return None
    from app.clients.gemini_client import GeminiJsonClient

    model = str(getattr(settings, "regime_llm_model", "") or "") or None
    try:
        client = GeminiJsonClient(api_key=api_key, model=model)
    except Exception:  # noqa: BLE001 — missing key/transport: degrade to OFF path
        return None
    return RegimeClassifier(client)
