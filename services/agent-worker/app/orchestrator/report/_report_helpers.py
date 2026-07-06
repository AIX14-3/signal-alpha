"""report 파이프라인 순수 헬퍼 — 방향/점수 매핑·요약·밸류에이션·직렬화(라우트·DB 비의존).

tasks.py 에서 재-import 되어 핸들러가 기존과 동일한 이름으로 참조한다(테스트 monkeypatch 호환).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

def _report_event_hash(raw_document_id: int) -> str:
    return hashlib.sha256(f"REPORT|{raw_document_id}".encode()).hexdigest()


def _report_consensus_direction(events: list[dict[str, Any]]) -> tuple[str, float, str]:
    """애널리스트 투자의견 컨센서스로 (direction, score[-1,1], data_status) 산출 — 결정론.

    각 이벤트의 ``signal_direction``(정규화가 투자의견에서 매핑: positive/negative/neutral/unknown)을
    모아 순매수도(=(긍정−부정)/방향성건수)를 점수로 쓴다. 방향성 의견이 하나도 없으면 features-only
    폴백(unknown/0/no_signal) — 회귀 없음. 임계 ±0.2 는 AGGREGATE 의 방향 판정과 정렬한다.
    """
    directions = [str(event.get("signal_direction") or "unknown").strip().lower() for event in events]
    directional = [d for d in directions if d in {"positive", "negative", "neutral"}]
    if not directional:
        return "unknown", 0.0, "no_signal"
    positive = directional.count("positive")
    negative = directional.count("negative")
    score = round((positive - negative) / len(directional), 3)
    if score >= 0.2:
        direction = "positive"
    elif score <= -0.2:
        direction = "negative"
    else:
        direction = "neutral"
    return direction, score, "ok"


def _report_signal_direction(opinion: Any) -> str:
    text = str(opinion or "").strip().lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("buy", "매수", "outperform", "strong")):
        return "positive"
    if any(token in text for token in ("sell", "매도", "reduce", "underperform")):
        return "negative"
    if any(token in text for token in ("hold", "neutral", "marketperform", "중립")):
        return "neutral"
    return "unknown"


def _report_impact_level(row: Mapping[str, Any]) -> str:
    if row.get("target_price") is not None or row.get("upside_pct") is not None:
        return "medium"
    return "low"


def _report_needs_review(row: Mapping[str, Any]) -> bool:
    return _report_signal_direction(row.get("investment_opinion")) == "unknown"


def _report_summary(row: Mapping[str, Any]) -> str:
    firm = str(row.get("securities_firm") or row.get("source_name") or "증권사")
    return f"{firm} 리포트에서 확인된 데이터 방향성입니다. 원문 근거와 소스 간 일치도 확인이 필요합니다."


def _report_evidence_text(row: Mapping[str, Any]) -> str:
    parts = [
        f"증권사: {row.get('securities_firm')}" if row.get("securities_firm") else "",
        f"원천 리포트 의견: {row.get('investment_opinion')}" if row.get("investment_opinion") else "",
        f"목표가: {row.get('target_price')}" if row.get("target_price") is not None else "",
        str(row.get("key_rationale") or "").strip(),
        str(row.get("extracted_text") or "").strip()[:500],
    ]
    return "\n".join(part for part in parts if part)


def _report_metrics(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for column, metric_name, metric_unit in (
        ("target_price", "report_target_price", "KRW"),
        ("previous_target_price", "report_previous_target_price", "KRW"),
        ("current_price_at_publish", "report_current_price_at_publish", "KRW"),
        ("upside_pct", "report_upside_pct", "percent"),
    ):
        value = row.get(column)
        if value is None:
            continue
        metrics.append({
            "metric_name": metric_name,
            "metric_value": value,
            "metric_unit": metric_unit,
        })
    return metrics


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _new_report_collection_result() -> dict[str, Any]:
    return {
        "raw_document_ids": [],
        "saved_reports": 0,
        "inserted_reports": 0,
        "duplicate_reports": 0,
        "invalid_date_reports": 0,
        "missing_pdf_reports": 0,
        "skip_reasons": {},
    }


def _report_collection_log_payload(
    *,
    status: str,
    collector_run_id: int,
    stock_id: int,
    stock_code: str,
    reports: list[dict],
    save_result: Mapping[str, Any],
    enqueued_count: int,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "collector_run_id": collector_run_id,
        "stock_id": stock_id,
        "stock_code": stock_code,
        "collected_reports": len(reports),
        "saved_reports": save_result["saved_reports"],
        "inserted_reports": save_result["inserted_reports"],
        "duplicate_reports": save_result["duplicate_reports"],
        "invalid_date_reports": save_result["invalid_date_reports"],
        "missing_pdf_reports": save_result["missing_pdf_reports"],
        "enqueued_reports": enqueued_count,
        "skip_reasons": save_result["skip_reasons"],
    }
    if error_message:
        payload["error_message"] = error_message
    return payload


def _compute_source_hash(report: dict, stock_id: int) -> str:
    """
    source_hash 규칙 (database/docs/source_hash_rule.md):
      REPORT|{stock_id}|{firm}|{title}|{publish_date}|{pdf_url}
    """
    parts = "|".join([
        "REPORT",
        str(stock_id),
        report.get("firm", ""),
        report.get("title", ""),
        str(report.get("date", "")),
        report.get("pdf_direct_url", "") or report.get("pdf_url", ""),
    ])
    return hashlib.sha256(parts.encode()).hexdigest()


def _parse_report_date(date_str: str) -> date | None:
    for fmt in ("%Y.%m.%d", "%y.%m.%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _source_raw_ids(value: Any) -> list[int]:
    return _int_list(value)


def _source_signal_event_ids(value: Any) -> list[int]:
    return _int_list(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            return [int(item.strip()) for item in inner.split(",") if item.strip()]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


def _extra_payload(value: Any) -> dict[str, Any]:
    """report_raw_details.extra_payload(JSONB)를 dict로 정규화.

    asyncpg는 JSONB 코덱을 등록하지 않으면 문자열로 돌려준다. str/dict/None 모두 수용.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _report_analysis_date(events: list[dict[str, Any]], task_context: dict[str, Any]) -> date:
    value = task_context.get("analysis_date") or task_context.get("event_date")
    if value:
        return _to_date(value)
    dates = [_to_date(event["event_date"]) for event in events if event.get("event_date")]
    return max(dates) if dates else date.today()


# Phase 0 (#525): _report_analysis_direction / _report_source_score(결정론 판정·스코어)는
# 제거됐다. 방향/점수는 학습형 메타러너 return 채널이 산출한다. 아래는 데이터 품질 플래그·요약만.


def _report_risk_flags(events: list[dict[str, Any]], needs_review: bool) -> list[str]:
    flags: list[str] = []
    if needs_review:
        flags.append("valuation_review_required")
    if any(event.get("target_price") is None for event in events):
        flags.append("target_price_missing")
    if any(event.get("implied_multiple") is None for event in events):
        flags.append("implied_multiple_missing")
    return flags


def _report_analysis_summary(events: list[dict[str, Any]], direction: str) -> str:
    brokers = sorted({str(event.get("broker") or "").strip() for event in events if event.get("broker")})
    broker_text = ", ".join(brokers[:3]) if brokers else "증권사 리포트"
    direction_text = {
        "positive": "긍정 방향",
        "negative": "주의 방향",
        "mixed": "혼재",
        "neutral": "중립",
    }.get(direction, "추가 확인 필요")
    return f"{broker_text} 자료의 밸류에이션 fact 기준 데이터 방향성은 {direction_text}입니다. 소스 간 일치도와 원문 근거 확인이 필요합니다."


def _report_valuation_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest = events[0]
    implied_values = [_float_or_none(event.get("implied_multiple")) for event in events]
    implied_values = [value for value in implied_values if value is not None]
    return {
        "target_price": _json_ready(latest.get("target_price")),
        "forward_eps_est": _json_ready(latest.get("forward_eps_est")),
        "eps_fy": _json_ready(latest.get("eps_fy")),
        "methodology": latest.get("methodology") or "unknown",
        "applied_multiple": _json_ready(latest.get("applied_multiple")),
        "implied_multiple": _json_ready(latest.get("implied_multiple")),
        "implied_multiple_avg": round(sum(implied_values) / len(implied_values), 4) if implied_values else None,
        "peer_group": _json_ready(_peer_group(latest.get("peer_group"))),
        "category_tag": latest.get("category_tag"),
        "rerating_thesis": latest.get("rerating_thesis"),
        "extraction_source": latest.get("extraction_source") or "rules",
        "needs_review": bool(latest.get("needs_review") or latest.get("fact_needs_review")),
        "event_count": len(events),
    }


def _report_evidence_quality(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    fact_count = sum(1 for event in events if event.get("raw_document_id") is not None)
    return round((fact_count / len(events)) * 100)


def _score_to_100(score: float) -> float:
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)


def _first_non_empty(events: list[dict[str, Any]], key: str) -> Any:
    for event in events:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _peer_group(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if hasattr(value, "as_tuple"):
        number = float(value)
        return int(number) if number.is_integer() else number
    return value
