"""소스 베이스라인 출력 계약 검증 — 대체데이터 베이스라인 authoring 가이드 + self-check.

대체데이터 소스(DART·REPORT·HIRING·PATENT·DATALAB)의 분석기는 ``agent_results.method_detail``
JSON 으로 결과를 낸다. 이 method_detail 이 곧 ``AggregateSignalTaskHandler`` 가 소비하는 **실질
계약**이다(``aggregation/tasks.py`` 의 ``_normalize_source_result`` 가 읽는 필드). 계약을 어기면
집계가 그 소스를 드롭하고 validation_log 에 남긴다 → 리포트에 해당 소스가 안 뜬다.

새 베이스라인을 만들 때 이 모듈의 ``validate_source_method_detail`` 로 산출물을 self-check 하면,
"집계가 내 소스를 소비할 수 있는가"를 DB 없이 즉시 확인할 수 있다. 규칙 상수는 aggregation 에서
재사용해(SOURCE_ALIASES/VALID_DIRECTIONS) 계약이 한쪽만 바뀌어 어긋나는 일을 막는다.

권장 method_detail 형태(소스 1건):

    {
        "source": "HIRING",            # 필수 — DART/REPORT/PRICE/HIRING/PATENT/DATALAB (대소문자 무관)
        "source_score": 0.32,           # 권장 — signed [-1, 1] (positive=호재, negative=주의)
        "direction": "positive",        # 권장 — positive/negative/neutral/mixed
        "data_status": "ok",            # 권장 — ok/partial/failed/no_signal
        "summary": "채용 공고 MoM +40%", # 권장 — 근거 한 줄(LLM 종합/리포트 카드에 노출)
        "risk_flags": [],               # 선택 — 주의 플래그 리스트
        "needs_review": false           # 선택 — 사람 검토 필요 여부
    }
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.aggregation.tasks import (
    SOURCE_ALIASES,
    VALID_DIRECTIONS,
    _normalize_source,
)

# data_status 허용값 — aggregation 의 _blend_status / _aggregate 가 의미를 부여하는 값들.
VALID_DATA_STATUS = {"ok", "partial", "failed", "no_signal"}


def validate_source_method_detail(
    detail: Any,
    *,
    method_signal: Any = None,
    method_score: Any = None,
) -> list[str]:
    """method_detail 이 집계 계약을 만족하는지 검사하고 **위반 사유 목록**을 반환(빈 리스트=통과).

    ``method_signal``/``method_score`` 는 agent_results 의 동명 컬럼 — detail 에 direction/score 가
    없을 때 aggregation 이 폴백으로 쓰므로, 같이 넘기면 더 정확히 판정한다.

    위반(집계가 소스를 드롭하는 치명적 문제)만 반환한다. 권장사항 미충족(summary 없음 등)은
    위반이 아니다 — 필요하면 ``method_detail_warnings`` 로 따로 점검.
    """
    violations: list[str] = []

    if not isinstance(detail, dict):
        return ["method_detail must be a JSON object (dict)."]

    # 1) source 해석 가능해야 한다(필수). 못 풀면 집계가 행을 드롭한다.
    source = None
    for key in ("source", "source_type"):
        source = _normalize_source(detail.get(key))
        if source is not None:
            break
    if source is None:
        violations.append(
            "source/source_type missing or unsupported — must resolve to one of "
            f"{sorted(set(SOURCE_ALIASES))}."
        )

    # 2) direction 이 있으면 허용값이어야 한다(없으면 aggregation 이 neutral 로 둠 → 위반 아님).
    direction = detail.get("direction") if detail.get("direction") is not None else method_signal
    if direction is not None and str(direction).strip().lower() not in VALID_DIRECTIONS:
        violations.append(
            f"direction '{direction}' invalid — use one of {sorted(VALID_DIRECTIONS)}."
        )

    # 3) score: source_score/score(또는 method_score 폴백) 중 하나는 숫자여야 점수에 반영된다.
    score_value = None
    for key in ("source_score", "score"):
        if detail.get(key) is not None:
            score_value = detail.get(key)
            break
    if score_value is None:
        score_value = method_score
    if score_value is None:
        violations.append(
            "no numeric score — provide method_detail.source_score (signed [-1,1]) "
            "or agent_results.method_score."
        )
    elif not _is_number(score_value):
        violations.append(f"score '{score_value}' is not numeric.")
    elif "source_score" in detail and not (-1.0 <= float(detail["source_score"]) <= 1.0):
        violations.append("source_score out of range — must be signed [-1.0, 1.0].")

    # 4) data_status 가 있으면 허용값이어야 한다(없으면 'ok' 로 간주 → 위반 아님).
    data_status = detail.get("data_status")
    if data_status is not None and str(data_status) not in VALID_DATA_STATUS:
        violations.append(
            f"data_status '{data_status}' invalid — use one of {sorted(VALID_DATA_STATUS)}."
        )

    # 5) risk_flags 는 있으면 리스트여야 한다.
    if detail.get("risk_flags") is not None and not isinstance(detail.get("risk_flags"), list):
        violations.append("risk_flags must be a list of strings when present.")

    return violations


def method_detail_warnings(detail: Any) -> list[str]:
    """위반은 아니지만 리포트 품질을 떨어뜨리는 권장사항 미충족을 반환(개발 보조)."""
    warnings: list[str] = []
    if not isinstance(detail, dict):
        return warnings
    if not (isinstance(detail.get("summary"), str) and detail.get("summary").strip()):
        warnings.append("summary 비어 있음 — 근거 한 줄이 있어야 LLM 종합/리포트 카드가 풍부해진다.")
    if detail.get("direction") is None:
        warnings.append("direction 미지정 — 명시하면 소스 방향성 일치도 계산이 정확해진다.")
    return warnings


def is_contract_compliant(
    detail: Any, *, method_signal: Any = None, method_score: Any = None
) -> bool:
    """위반이 없으면 True(베이스라인 self-check 단축형)."""
    return not validate_source_method_detail(
        detail, method_signal=method_signal, method_score=method_score
    )


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
