"""레퍼런스 채점기 — 백테스트 **계측 전용** 결정론 스코어러.

## 왜 이 모듈이 있는가

점수 산출이 LLM 으로 넘어가면 서빙 경로에서 결정론 수식(``analyzers/scoring.graded`` +
소스별 ``rules.evaluate_indicators``)은 사라진다. 그런데 그 수식은 **백테스트의 계측기**
이기도 하다: ``scripts/recompute_source_ic.py`` 가 과거 asof 에서 소스 점수를 재계산해
forward return 과의 IC 를 잰다.

여기서 재는 것은 "이 채점기가 좋은가"가 아니라 **"이 데이터에 알파가 있는가"** 다. 채점기는
데이터를 숫자로 바꾸는 자(ruler)일 뿐이므로, 서빙에서 빠져도 계측기로는 남아야 한다.

**LLM 점수로는 이 계측을 할 수 없다** — 2025년까지 학습한 모델에게 2021년 시점을 채점시키면
모델이 이미 결과를 안다(세계지식 누수). 프롬프트로 막을 수 없다. 따라서 LLM 점수의 과거 IC 는
원리적으로 신뢰 불가이고, **go-live 이후 전진(forward-only) 평가만** 유효하다. 과거 데이터에
알파가 있는지는 계속 이 결정론 레퍼런스로 잰다.

## shadow 가 아니다
프로덕션에서 실행되지 않고 발행물에 영향을 주지 않는다. 파이프라인의 어떤 핸들러도 이 모듈을
부르지 않는다 — 오직 ``scripts/`` 의 연구 하니스만 부른다.

## 현재는 façade
지금은 아직 서빙 경로에 결정론 분석기가 살아 있으므로 그쪽을 재사용한다(코드 중복 없음).
서빙에서 ``rules.py`` / ``scoring.py`` 를 삭제하는 시점에 **본체를 이 파일로 옮기면** 되고,
호출측(``scripts/recompute_source_ic.py``)은 손대지 않는다. 이 파일이 그 이음매(seam)다.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, Callable

from app.analyzers.config import (
    DartRuleConfig,
    DataLabRuleConfig,
    HiringRuleConfig,
    PatentRuleConfig,
)
from app.analyzers.dart.source_result import build_dart_analysis_result
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators
from app.analyzers.datalab.rules import evaluate_indicators as datalab_eval
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators
from app.analyzers.hiring.rules import evaluate_indicators as hiring_eval
from app.analyzers.patent.indicators import compute_indicators as patent_indicators
from app.analyzers.patent.rules import evaluate_indicators as patent_eval
from app.analyzers.price.analyzer import PriceAnalyzer
from app.ml.source_features import KNOWN_AT

# (SRC, kind, loader_key, date_key, indicators_fn, eval_fn, config_cls)
#   kind=rules : compute_indicators + evaluate_indicators (대체데이터 3소스)
#   kind=dart  : build_dart_analysis_result(events).score
#   kind=price : PriceAnalyzer().analyze  (⚠️ 자기참조: 과거가격→미래가격, 대체데이터 알파 아님)
# REPORT 제외: 결정론 분석기/로더 경로가 없다(밸류에이션 별도 경로).
SOURCES: list[tuple[str, str, str, str, Any, Any, Any]] = [
    ("PATENT", "rules", "patent", KNOWN_AT["patent"], patent_indicators, patent_eval, PatentRuleConfig),
    ("DATALAB", "rules", "datalab", KNOWN_AT["datalab"], datalab_indicators, datalab_eval, DataLabRuleConfig),
    ("HIRING", "rules", "hiring", KNOWN_AT["hiring"], hiring_indicators, hiring_eval, HiringRuleConfig),
    ("DART", "dart", "dart", KNOWN_AT["dart"], None, None, DartRuleConfig),
    ("PRICE", "price", "price", KNOWN_AT["price"], None, None, None),
]


def dart_blite_events(pit: list[dict]) -> list[dict]:
    """DART ownership 이벤트에 B-lite 방향/임팩트를 부여한다(프로덕션 코드 불변, 하니스 전용).

    ``build_dart_analysis_result`` 의 B-lite 는 ``event['signal_direction']``·``['impact_level']``
    로 임팩트 가중 순극성을 낸다. 수집된 ownership 이벤트엔 이 파생 필드가 없어 상수 0(no_signal)
    이 된다. 소스 주석(``analyzers/dart/source_result.py``)이 명시한 대로 **내부자 shares_delta
    부호**를 방향으로, ``ratio_delta`` 크기를 임팩트로 매핑해 B-lite 경로를 활성화한다.
    """
    out: list[dict] = []
    for event in pit:
        row = dict(event)
        shares_delta = event.get("shares_delta")
        row["signal_direction"] = (
            "positive"
            if (shares_delta is not None and float(shares_delta) > 0)
            else "negative"
            if (shares_delta is not None and float(shares_delta) < 0)
            else "unknown"
        )
        ratio_delta = event.get("ratio_delta")
        magnitude = abs(float(ratio_delta)) if ratio_delta is not None else 0.0
        row["impact_level"] = (
            "high" if magnitude >= 1.0 else "medium" if magnitude >= 0.2 else "low"
        )
        out.append(row)
    return out


async def score_source(
    kind: str,
    pit: list[dict],
    asof: date,
    cfg: Any,
    sector: Any = None,
    ind_fn: Callable[..., Any] | None = None,
    eval_fn: Callable[..., Any] | None = None,
) -> float:
    """소스 kind 별 결정론 점수 재계산. ``pit`` 은 누수차단된 행. signed ``[-1, +1]`` 반환."""
    if kind == "dart":
        return build_dart_analysis_result(dart_blite_events(pit)).score
    if kind == "price":
        result = await PriceAnalyzer().analyze("", [SimpleNamespace(metadata={"rows": pit})])
        return result.score
    # rules (patent / datalab / hiring)
    if ind_fn is None or eval_fn is None:
        raise ValueError(f"rules kind requires ind_fn/eval_fn (kind={kind})")
    lookback = getattr(cfg, "lookback_days", 30)
    if ind_fn is hiring_indicators:
        indicators = ind_fn(pit, as_of=asof, lookback_days=lookback, sector_demand=sector)
    else:
        indicators = ind_fn(pit, as_of=asof, lookback_days=lookback)
    return eval_fn(indicators, cfg).score
