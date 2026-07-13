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

## 수식 본체는 ``app.backtest.reference_rules`` (2026-07-13 역전 완료)
서빙 점수가 LLM 채점(SCORE_COHORT)으로 전환되면서 수식 본체를 ``reference_rules/`` 로
옮겼다 — 이제 계측기가 수식의 단일 거처다. 서빙 쪽 ``app/analyzers/*/rules.py`` /
``analyzers/scoring.py`` 는 reference_rules 를 재수출하는 얇은 façade 로 남아
기존 경로·테스트·LLM 폴백(LLM_SCORING_FALLBACK=rules)이 무변경으로 동작한다.
호출측(``scripts/recompute_source_ic.py``)은 손대지 않았다.
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
    ReportRuleConfig,
)
# DART 는 수식(reference_rules.dart_score)이 아니라 조립까지 포함한 build_dart_analysis_result
# 를 쓴다 — 이벤트 순회/카운트는 source_result 에 남아 있고 수식만 reference_rules 로 갔다.
from app.analyzers.dart.source_result import build_dart_analysis_result
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators
from app.analyzers.patent.indicators import compute_indicators as patent_indicators
from app.backtest.reference_rules.datalab import evaluate_indicators as datalab_eval
from app.backtest.reference_rules.hiring import evaluate_indicators as hiring_eval
from app.backtest.reference_rules.patent import evaluate_indicators as patent_eval
from app.backtest.reference_rules.report import evaluate_report
from app.analyzers.price.analyzer import PriceAnalyzer
from app.ml.source_features import KNOWN_AT

# (SRC, kind, loader_key, date_key, indicators_fn, eval_fn, config_cls)
#   kind=rules  : compute_indicators + evaluate_indicators (대체데이터 3소스)
#   kind=dart   : build_dart_analysis_result(events).score
#   kind=report : evaluate_report(revision_pct, upside_pct) — 목표주가 리비전 + upside
#   kind=price  : PriceAnalyzer().analyze  (⚠️ 자기참조: 과거가격→미래가격, 대체데이터 알파 아님)
SOURCES: list[tuple[str, str, str, str, Any, Any, Any]] = [
    ("PATENT", "rules", "patent", KNOWN_AT["patent"], patent_indicators, patent_eval, PatentRuleConfig),
    ("DATALAB", "rules", "datalab", KNOWN_AT["datalab"], datalab_indicators, datalab_eval, DataLabRuleConfig),
    ("HIRING", "rules", "hiring", KNOWN_AT["hiring"], hiring_indicators, hiring_eval, HiringRuleConfig),
    ("DART", "dart", "dart", KNOWN_AT["dart"], None, None, DartRuleConfig),
    ("REPORT", "report", "report", KNOWN_AT["report"], None, None, ReportRuleConfig),
    ("PRICE", "price", "price", KNOWN_AT["price"], None, None, None),
]


def report_indicators(pit: list[dict], current_close: float | None) -> tuple[float | None, float | None]:
    """목표주가 이력 → (revision_pct, upside_pct). 순수 산술 — 판단 없음.

    ``pit`` 은 ``report_valuation_facts`` 행(``publish_date``·``target_price``)이며 이미 PIT
    필터를 통과했다. 같은 날 여러 증권사가 내면 평균을 그날의 목표가로 본다.
    """
    by_day: dict[str, list[float]] = {}
    for row in pit:
        target = row.get("target_price")
        day = row.get("publish_date")
        if target is None or day is None:
            continue
        by_day.setdefault(str(day)[:10], []).append(float(target))
    if not by_day:
        return None, None
    ordered = sorted(by_day.items())
    latest = sum(ordered[-1][1]) / len(ordered[-1][1])
    revision = None
    if len(ordered) >= 2:
        prior = sum(ordered[-2][1]) / len(ordered[-2][1])
        if prior > 0:
            revision = (latest - prior) / prior
    upside = (latest - current_close) / current_close if (current_close and current_close > 0) else None
    return revision, upside


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
    current_close: float | None = None,
) -> float:
    """소스 kind 별 결정론 점수 재계산. ``pit`` 은 누수차단된 행. signed ``[-1, +1]`` 반환."""
    if kind == "dart":
        return build_dart_analysis_result(dart_blite_events(pit)).score
    if kind == "report":
        revision, upside = report_indicators(pit, current_close)
        return evaluate_report(revision_pct=revision, upside_pct=upside, config=cfg).score
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
