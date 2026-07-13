"""정규화 증거 → LLM ``StockContext`` 입력 압축 (연구 러너 ``cohort_llm_run.py`` 승격).

원칙: **"코드는 세고, LLM 은 판단한다."**
  준다   → 시계열·목록 그 자체 + **산술 집계**(카운트·합계·월별 버킷)
  안 준다 → 사람이 정한 **판단성 파생비율**(momentum_pct·spike_ratio·net_polarity)
           그리고 데이터 적재량 아티팩트(행 개수 같은 것 — LLM 이 가짜 신호로 오용한다)

DATALAB attention 은 코드가 계산한 매그니튜드 축(유일 실증신호)을 **읽기 전용**으로만
붙인다 — LLM 출력 스키마에는 attention 필드가 아예 없어 오염 수단이 없다.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab.attention import compute_attention_spike

MAX_ITEMS = 40


def _d(v: Any) -> str:
    return str(v)[:10]


def _monthly_avg(series: list[tuple[str, float]]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for day, value in series:
        buckets.setdefault(day[:7], []).append(value)
    return [{"month": m, "avg": round(sum(v) / len(v), 2)} for m, v in sorted(buckets.items())]


def build_evidence(src: str, pit: list[dict], close: float | None) -> tuple[dict, dict]:
    """PIT 필터된 정규화 행 → (evidence, self_history). 소스별 압축 규칙은 실측 검증본."""
    if src == "DATALAB":
        by_pol: dict[str, dict[str, list[float]]] = {"demand": {}, "risk": {}}
        for r in pit:
            if r.get("search_index") is None:
                continue
            pol = "risk" if (r.get("polarity") == "risk") else "demand"
            by_pol[pol].setdefault(_d(r.get("observed_date")), []).append(float(r["search_index"]))
        daily = {
            p: sorted((d, round(sum(v) / len(v), 2)) for d, v in days.items())
            for p, days in by_pol.items()
        }
        ev: dict = {"demand_search_series_recent_60d": daily["demand"][-60:]}
        hist: dict = {"demand_search_monthly_avg_prior_12m": _monthly_avg(daily["demand"])[-12:]}
        if daily["risk"]:
            ev["risk_search_series_recent_60d"] = daily["risk"][-60:]
            hist["risk_search_monthly_avg_prior_12m"] = _monthly_avg(daily["risk"])[-12:]
        else:
            ev["risk_keywords_tracked"] = 0
            ev["composition_measurable"] = False
            ev["note"] = "risk 극성 키워드가 등록돼 있지 않아 demand vs risk 구성 변화를 측정할 수 없다."
        return ev, hist

    if src == "PATENT":
        pubs = sorted(pit, key=lambda r: str(r.get("publication_date") or ""), reverse=True)
        months = Counter(_d(r.get("publication_date"))[:7] for r in pit if r.get("publication_date"))
        cats = Counter(r.get("tech_category") for r in pit if r.get("tech_category"))
        ev = {
            "publications_recent_sample": [
                {"title": (r.get("title") or "")[:120],
                 "publication_date": _d(r.get("publication_date")),
                 "tech_category": r.get("tech_category")}
                for r in pubs[:MAX_ITEMS]
            ],
            "sample_note": f"위는 최근 {min(len(pubs), MAX_ITEMS)}건 표본. 창 전체 {len(pit)}건은 아래 집계 참조.",
            "publications_by_month_full_window": dict(sorted(months.items())[-24:]),
            "top_tech_categories_full_window": dict(cats.most_common(10)),
        }
        years = Counter(_d(r.get("application_date"))[:4] for r in pit if r.get("application_date"))
        return ev, {"filings_by_year": dict(sorted(years.items()))}

    if src == "HIRING":
        posts = sorted(pit, key=lambda r: str(r.get("observed_date") or ""), reverse=True)
        months = Counter(_d(r.get("observed_date"))[:7] for r in pit if r.get("observed_date"))
        ev = {
            "postings_recent_sample": [
                {"title": (r.get("job_title") or r.get("title") or "")[:100],
                 "observed_date": _d(r.get("observed_date"))}
                for r in posts[:MAX_ITEMS]
            ],
            "total_rows_in_window": len(pit),
            "postings_by_month_full_window": dict(sorted(months.items())[-24:]),
        }
        return ev, {"postings_by_month": dict(sorted(months.items()))}

    if src == "DART":
        evs = sorted(pit, key=lambda r: str(r.get("report_date") or ""), reverse=True)

        def _sd(row: dict) -> float:
            v = row.get("shares_delta")
            return float(v) if v is not None else 0.0

        ev = {
            "events_recent_sample": [
                {"report_date": _d(r.get("report_date")),
                 "holder": r.get("holder_name") or r.get("holder_type"),
                 "shares_delta": r.get("shares_delta"),
                 "ratio_delta": float(r["ratio_delta"]) if r.get("ratio_delta") is not None else None}
                for r in evs[:MAX_ITEMS]
            ],
            "sample_note": f"위는 최근 {min(len(evs), MAX_ITEMS)}건 표본. 아래 집계가 창 전체({len(pit)}건)를 반영한다.",
            "aggregate_over_full_window": {
                "total_events": len(pit),
                "events_with_share_increase": sum(1 for r in pit if _sd(r) > 0),
                "events_with_share_decrease": sum(1 for r in pit if _sd(r) < 0),
                "net_shares_delta": int(sum(_sd(r) for r in pit)),
            },
        }
        months = Counter(_d(r.get("report_date"))[:7] for r in pit if r.get("report_date"))
        return ev, {"events_by_month": dict(sorted(months.items())[-12:])}

    if src == "REPORT":
        by_day: dict[str, list[dict]] = {}
        for r in pit:
            if r.get("target_price") is None or r.get("publish_date") is None:
                continue
            by_day.setdefault(_d(r["publish_date"]), []).append(
                {"broker": r.get("broker"), "target_price": int(r["target_price"])}
            )
        ordered = sorted(by_day.items())
        ev = {
            "target_price_history": [
                {"date": d, "targets": items, "avg_target": round(
                    sum(i["target_price"] for i in items) / len(items))}
                for d, items in ordered[-20:]
            ],
            "current_close": close,
            "distinct_publish_dates": len(ordered),
        }
        return ev, {"target_price_history_full": [
            {"date": d, "avg_target": round(sum(i["target_price"] for i in items) / len(items))}
            for d, items in ordered
        ]}

    if src == "PRICE":
        rows = sorted(pit, key=lambda r: str(r.get("trade_date") or ""))
        ev = {
            "ohlcv_recent_60d": [
                {"d": _d(r.get("trade_date")), "close": float(r["close"]),
                 "volume": int(r["volume"]) if r.get("volume") is not None else None,
                 "foreign_net": int(r["foreign_net"]) if r.get("foreign_net") is not None else None,
                 "institution_net": int(r["institution_net"]) if r.get("institution_net") is not None else None}
                for r in rows[-60:]
            ]
        }
        closes = [(_d(r.get("trade_date")), float(r["close"])) for r in rows]
        return ev, {"close_monthly_avg_prior_12m": _monthly_avg(closes)[-12:]}

    return {"rows": len(pit)}, {}


def build_attention(pit: list[dict], asof: date) -> dict | None:
    """DATALAB 전용: 코드가 계산한 attention 매그니튜드 축(읽기 전용 컨텍스트).

    LLM 출력 스키마에는 attention 필드가 없다 — 쓸 수단이 없으면 오염 못 시킨다.
    """
    series = {
        _d(r.get("observed_date")): float(r["search_index"])
        for r in pit
        if r.get("search_index") is not None and (r.get("polarity") or "demand") != "risk"
    }
    spike = compute_attention_spike(series, as_of=asof, config=DataLabRuleConfig.from_env())
    if spike is None:
        return None
    return {"z": round(spike.attention_z, 2), "tier": spike.attention_tier,
            "meaning": "향후 거래량·변동성 증가 예상. 방향 정보 아님."}
