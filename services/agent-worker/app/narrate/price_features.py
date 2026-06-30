"""PRICE 메타 피처 결정론 서술 — 메타러너 주가 base 모델이 보는 피처(ret_5d/rsi14/이동평균
갭/수급 연속·비중/변동성/크로스)를 **사람이 읽기 쉬운 한국어 근거**로 풀어 쓴다.

LLM 없이 동작한다. ``contributions``(LightGBM ``pred_contrib``)가 주어지면 그 절댓값으로
상위 기여 피처를 골라 "왜 이 예측인지"를 모델 근거 순으로 보여주고, 없으면 피처 현저성
(magnitude)으로 고른다. 수치는 발행 피처를 그대로 풀어 쓸 뿐 새로 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.narrate.base import SourceNarrative

# 강세 RSI 하한/과매수·과매도 경계.
_RSI_OVERBOUGHT = 70.0
_RSI_OVERSOLD = 30.0
_RSI_BULL = 55.0
# 현저성 임계값 — 이 정도는 돼야 "근거"로 노출(자잘한 값은 제외).
_RET_MIN = 0.02  # 2%
_GAP_MIN = 0.01  # 1%
_VOL_HIGH = 3.0  # 20일 일간변동성(%)
_VOLZ_SPIKE = 2.0
_STREAK_MIN = 3
_FLOW_MIN = 0.005


def _pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _fact(text: str, salience: float) -> tuple[str, float]:
    return (text, salience)


def _feature_facts(features: Mapping[str, Any]) -> dict[str, tuple[str, float]]:
    """피처값 → {피처키: (한국어 문장, 현저성)}. 주목할 만한 피처만 포함한다."""
    out: dict[str, tuple[str, float]] = {}

    def num(key: str) -> float | None:
        v = features.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    ret5 = num("ret_5d")
    if ret5 is not None and abs(ret5) >= _RET_MIN:
        trend = "상승" if ret5 > 0 else "하락"
        out["ret_5d"] = _fact(
            f"최근 5영업일 수익률이 {_pct(ret5)}로 단기적으로 {trend} 흐름입니다.", abs(ret5)
        )
    ret20 = num("ret_20d")
    if ret20 is not None and abs(ret20) >= _RET_MIN:
        trend = "상승" if ret20 > 0 else "하락"
        out["ret_20d"] = _fact(
            f"최근 약 한 달(20영업일) 수익률이 {_pct(ret20)}로 {trend} 추세입니다.", abs(ret20)
        )
    gap = num("close_sma20_gap")
    if gap is not None and abs(gap) >= _GAP_MIN:
        side = "웃돌아" if gap > 0 else "밑돌아"
        tone = "중기 흐름이 양호합니다" if gap > 0 else "중기 흐름이 약합니다"
        out["close_sma20_gap"] = _fact(
            f"현재가가 20일 이동평균선을 {_pct(abs(gap))} {side} {tone}.", abs(gap)
        )
    s5 = num("sma5_sma20_gap")
    if s5 is not None and abs(s5) >= _GAP_MIN:
        if s5 > 0:
            out["sma5_sma20_gap"] = _fact("5일선이 20일선 위에 있어 단기 추세가 상승(정배열)입니다.", abs(s5))
        else:
            out["sma5_sma20_gap"] = _fact("5일선이 20일선 아래에 있어 단기 추세가 하락(역배열)입니다.", abs(s5))
    s20 = num("sma20_sma60_gap")
    if s20 is not None and abs(s20) >= _GAP_MIN:
        if s20 > 0:
            out["sma20_sma60_gap"] = _fact("20일선이 60일선 위에 있어 중기 추세가 상승 우위입니다.", abs(s20))
        else:
            out["sma20_sma60_gap"] = _fact("20일선이 60일선 아래에 있어 중기 추세가 하락 우위입니다.", abs(s20))
    rsi = num("rsi14")
    if rsi is not None:
        if rsi >= _RSI_OVERBOUGHT:
            out["rsi14"] = _fact(f"RSI가 {rsi:.0f}로 과매수 구간입니다(단기 과열 주의).", 0.9)
        elif rsi <= _RSI_OVERSOLD:
            out["rsi14"] = _fact(f"RSI가 {rsi:.0f}로 과매도 구간입니다(단기 낙폭 과대).", 0.9)
        elif rsi >= _RSI_BULL:
            out["rsi14"] = _fact(f"RSI가 {rsi:.0f}로 강세 구간에 있습니다.", 0.5)
    volz = num("volume_z")
    if volz is not None and volz >= _VOLZ_SPIKE:
        out["volume_z"] = _fact("최근 거래량이 평소(20일 평균)보다 크게 늘었습니다(거래 급증).", min(volz / 3.0, 1.0))
    vol = num("volatility20")
    if vol is not None and vol >= _VOL_HIGH:
        out["volatility20"] = _fact(
            f"최근 변동성이 확대돼 가격 등락 폭이 큽니다(20일 변동성 {vol:.1f}%).", min(vol / 5.0, 1.0)
        )
    if num("golden_cross"):
        out["golden_cross"] = _fact("최근 5·20일 이동평균이 골든크로스해 상승 전환 신호가 나왔습니다.", 0.8)
    if num("dead_cross"):
        out["dead_cross"] = _fact("최근 5·20일 이동평균이 데드크로스해 하락 전환 신호가 나왔습니다.", 0.8)
    fstreak = num("foreign_streak")
    if fstreak is not None and abs(fstreak) >= _STREAK_MIN:
        if fstreak > 0:
            out["foreign_streak"] = _fact(f"외국인이 {int(fstreak)}영업일 연속 순매수 중입니다.", min(abs(fstreak) / 5.0, 1.0))
        else:
            out["foreign_streak"] = _fact(f"외국인이 {int(abs(fstreak))}영업일 연속 순매도 중입니다.", min(abs(fstreak) / 5.0, 1.0))
    istreak = num("institution_streak")
    if istreak is not None and abs(istreak) >= _STREAK_MIN:
        if istreak > 0:
            out["institution_streak"] = _fact(f"기관이 {int(istreak)}영업일 연속 순매수 중입니다.", min(abs(istreak) / 5.0, 1.0))
        else:
            out["institution_streak"] = _fact(f"기관이 {int(abs(istreak))}영업일 연속 순매도 중입니다.", min(abs(istreak) / 5.0, 1.0))
    fflow = num("foreign_flow_ratio20")
    if fflow is not None and abs(fflow) >= _FLOW_MIN:
        side = "순매수" if fflow > 0 else "순매도"
        out["foreign_flow_ratio20"] = _fact(f"최근 한 달 외국인 수급이 {side} 우위입니다.", min(abs(fflow) * 50, 1.0))
    iflow = num("institution_flow_ratio20")
    if iflow is not None and abs(iflow) >= _FLOW_MIN:
        side = "순매수" if iflow > 0 else "순매도"
        out["institution_flow_ratio20"] = _fact(f"최근 한 달 기관 수급이 {side} 우위입니다.", min(abs(iflow) * 50, 1.0))
    return out


_DIRECTION_KO = {"positive": "긍정", "negative": "부정", "neutral": "중립", "mixed": "혼조"}


def narrate_price_features(
    *,
    features: Mapping[str, Any],
    contributions: Mapping[str, float] | None = None,
    direction: str | None = None,
    max_facts: int = 4,
) -> SourceNarrative | None:
    """주가 메타 피처 → 결정론 서술. 주목할 피처가 없으면 None(서술 생략).

    ``contributions`` 가 있으면 |기여도| 로 상위 피처를 정렬(모델이 실제로 본 근거 순),
    없으면 피처 현저성으로 정렬한다.
    """
    facts = _feature_facts(features)
    if not facts:
        return None

    def weight(key: str) -> float:
        if contributions is not None:
            c = contributions.get(key)
            if c is not None:
                return abs(float(c))
        return facts[key][1]

    ranked = sorted(facts.keys(), key=weight, reverse=True)
    key_facts = [facts[k][0] for k in ranked[:max_facts]]

    dir_ko = _DIRECTION_KO.get(str(direction or "").lower())
    lead = (
        f"주가 흐름과 거래 수급을 종합하면 {dir_ko} 신호입니다."
        if dir_ko
        else "주가 흐름과 거래 수급을 종합한 분석 근거입니다."
    )
    basis = "모델이 가장 크게 반영한 지표" if contributions is not None else "두드러진 지표"
    summary = f"{lead} 아래는 이 판단에 {basis} 순서입니다."
    return SourceNarrative(summary=summary, key_facts=key_facts)
