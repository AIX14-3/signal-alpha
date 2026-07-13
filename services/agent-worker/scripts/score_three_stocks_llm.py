"""3종목 LLM 코호트 채점 — 결정론(수식) 점수와 나란히 비교.

같은 (종목, asof) 에서 **같은 정규화 증거**를 두 채점기에 먹인다:
  결정론: pit_rows → indicators(파생비율) → graded(tanh) → score
  LLM   : pit_rows → 압축된 정규화 증거 + 자기과거 + 코호트 → LLM 이 직접 판단 → score

LLM 에는 파생 비율(momentum_pct 등)을 **주지 않는다** — 그건 사람이 정한 기준이고, 주면 남의
판단을 베끼게 된다. 시계열·목록 그 자체를 주고 형태를 스스로 읽게 한다.

⚠️ DATALAB 의 attention(매그니튜드)은 코드가 계산해 **읽기 전용**으로만 준다. LLM 출력 스키마엔
attention 필드가 없다 — 쓸 수 없으면 오염시킬 수 없다.

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    export GEMINI_API_KEY=...
    python scripts/score_three_stocks_llm.py --model gemini-2.5-flash
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))

import asyncpg  # noqa: E402

from app.analyzers.config import DataLabRuleConfig  # noqa: E402
from app.analyzers.datalab.attention import compute_attention_spike  # noqa: E402
from app.analyzers.llm_scorer import StockContext, score_cohort  # noqa: E402
from app.backtest.reference_scorer import SOURCES, score_source  # noqa: E402
from app.clients.gemini_client import GeminiJsonClient  # noqa: E402
from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TICKERS = ["005930", "000660", "035420"]
MAX_ITEMS = 40  # 목록형 증거를 프롬프트에 넣을 최대 건수(최신순)


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


def _d(value) -> str:
    return str(value)[:10]


# --- 정규화 증거 압축: 파생 '판단' 비율은 빼고, 사실만 담는다 -----------------------
def _evidence_for(src: str, pit: list[dict], asof: date) -> tuple[dict, dict]:
    """(evidence, self_history) — evidence=최근 관측, self_history=자기 과거 base rate."""
    if src == "DATALAB":
        # 극성별 **검색 지수** 시계열. (행 개수는 절대 주지 않는다 — 그건 '몇 개 키워드를
        # 추적 중인가'라는 데이터 적재량 아티팩트이지 신호가 아니다. 처음 버전에서 이걸
        # 넣었더니 LLM 이 그걸 주 근거로 삼아 가짜 점수를 만들었다.)
        by_pol: dict[str, dict[str, list[float]]] = {"demand": {}, "risk": {}}
        for r in pit:
            if r.get("search_index") is None:
                continue
            pol = "risk" if (r.get("polarity") == "risk") else "demand"
            by_pol[pol].setdefault(_d(r.get("observed_date")), []).append(float(r["search_index"]))
        daily = {
            pol: sorted((d, round(sum(v) / len(v), 2)) for d, v in days.items())
            for pol, days in by_pol.items()
        }
        ev: dict = {"demand_search_series_recent_60d": daily["demand"][-60:]}
        hist: dict = {"demand_search_monthly_avg_prior_12m": _monthly_avg(daily["demand"])[-12:]}
        if daily["risk"]:
            ev["risk_search_series_recent_60d"] = daily["risk"][-60:]
            hist["risk_search_monthly_avg_prior_12m"] = _monthly_avg(daily["risk"])[-12:]
        else:
            # 구성 변화를 **측정할 수 없다**는 사실을 명시적으로 알린다 → LLM 이 지어내지 않게.
            ev["risk_keywords_tracked"] = 0
            ev["composition_measurable"] = False
            ev["note"] = (
                "이 종목엔 risk(리스크) 극성 키워드가 하나도 등록돼 있지 않다. "
                "따라서 demand vs risk 구성 변화를 측정할 수 없다."
            )
        return ev, hist

    if src == "PATENT":
        pubs = sorted(pit, key=lambda r: str(r.get("publication_date") or ""), reverse=True)
        months = Counter(
            _d(r.get("publication_date"))[:7] for r in pit if r.get("publication_date")
        )
        cats = Counter(r.get("tech_category") for r in pit if r.get("tech_category"))
        ev = {
            "publications_recent_sample": [
                {
                    "title": (r.get("title") or "")[:120],
                    "publication_date": _d(r.get("publication_date")),
                    "application_date": _d(r.get("application_date")),
                    "tech_category": r.get("tech_category"),
                }
                for r in pubs[:MAX_ITEMS]
            ],
            "sample_note": (
                f"위는 최근 {min(len(pubs), MAX_ITEMS)}건 **표본**이다. 창 전체는 {len(pit)}건이며 "
                f"아래 월별 집계가 전체를 반영한다."
            ),
            "publications_by_month_full_window": dict(sorted(months.items())[-24:]),
            "top_tech_categories_full_window": dict(cats.most_common(10)),
        }
        years = Counter(_d(r.get("application_date"))[:4] for r in pit if r.get("application_date"))
        hist = {"filings_by_year": dict(sorted(years.items()))}
        return ev, hist

    if src == "HIRING":
        posts = sorted(pit, key=lambda r: str(r.get("observed_date") or ""), reverse=True)
        ev = {
            "postings_recent": [
                {"title": (r.get("job_title") or r.get("title") or "")[:100],
                 "observed_date": _d(r.get("observed_date"))}
                for r in posts[:MAX_ITEMS]
            ],
            "total_rows_in_window": len(pit),
        }
        months = Counter(_d(r.get("observed_date"))[:7] for r in pit if r.get("observed_date"))
        hist = {"postings_by_month": dict(sorted(months.items()))}
        return ev, hist

    if src == "DART":
        evs = sorted(pit, key=lambda r: str(r.get("report_date") or ""), reverse=True)
        # ⚠️ 최근 N건만 보여주면 truncation bias 가 생긴다(결정론 채점기는 전 이벤트를 본다).
        # 그래서 **집계치를 함께** 준다 — 집계는 '세기'이지 '판단'이 아니므로 이 원칙에 어긋나지
        # 않는다("코드는 세고, LLM 은 판단한다").
        def _sd(row) -> float:
            value = row.get("shares_delta")
            return float(value) if value is not None else 0.0

        buys = [r for r in pit if _sd(r) > 0]
        sells = [r for r in pit if _sd(r) < 0]
        ev = {
            "events_recent_sample": [
                {
                    "report_date": _d(r.get("report_date")),
                    "holder": r.get("holder_name") or r.get("holder_type"),
                    "shares_delta": r.get("shares_delta"),
                    "ratio_delta": float(r["ratio_delta"]) if r.get("ratio_delta") is not None else None,
                }
                for r in evs[:MAX_ITEMS]
            ],
            "sample_note": (
                f"위는 최근 {min(len(evs), MAX_ITEMS)}건만 보여준 **표본**이다. "
                f"창 전체는 {len(pit)}건이며, 아래 집계가 전체를 반영한다."
            ),
            "aggregate_over_full_window": {
                "total_events": len(pit),
                "events_with_share_increase": len(buys),
                "events_with_share_decrease": len(sells),
                "net_shares_delta": int(sum(_sd(r) for r in pit)),
            },
        }
        months = Counter(_d(r.get("report_date"))[:7] for r in pit if r.get("report_date"))
        hist = {"events_by_month": dict(sorted(months.items())[-12:])}
        return ev, hist

    if src == "PRICE":
        rows = sorted(pit, key=lambda r: str(r.get("trade_date") or ""))
        ev = {
            "ohlcv_recent_60d": [
                {
                    "d": _d(r.get("trade_date")),
                    "close": float(r["close"]),
                    "volume": int(r["volume"]) if r.get("volume") is not None else None,
                    "foreign_net": int(r["foreign_net"]) if r.get("foreign_net") is not None else None,
                    "institution_net": (
                        int(r["institution_net"]) if r.get("institution_net") is not None else None
                    ),
                }
                for r in rows[-60:]
            ]
        }
        closes = [(_d(r.get("trade_date")), float(r["close"])) for r in rows]
        hist = {"close_monthly_avg_prior_12m": _monthly_avg(closes)[-12:]}
        return ev, hist

    return {"rows": len(pit)}, {}


def _monthly_avg(series: list[tuple[str, float]]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for day, value in series:
        buckets.setdefault(day[:7], []).append(value)
    return [{"month": m, "avg": round(sum(v) / len(v), 2)} for m, v in sorted(buckets.items())]


def _attention_for(pit: list[dict], asof: date) -> dict | None:
    """코드가 계산하는 매그니튜드 축. LLM 은 읽기만 한다(출력 스키마에 없음)."""
    series = {
        _d(r.get("observed_date")): float(r["search_index"])
        for r in pit
        if r.get("search_index") is not None and (r.get("polarity") or "demand") != "risk"
    }
    spike = compute_attention_spike(series, as_of=asof, config=DataLabRuleConfig.from_env())
    if spike is None:
        return None
    return {
        "z": round(spike.attention_z, 2),
        "tier": spike.attention_tier,
        "meaning": "향후 거래량·변동성 증가 예상. 방향 정보 아님.",
    }


async def run(asof: date | None, model: str, out_json: str | None) -> None:
    db_url = os.environ["DATABASE_URL"]
    _require_local(db_url)
    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY required")
    client = GeminiJsonClient(model=model, temperature=0.0)

    conn = await asyncpg.connect(db_url)
    try:
        from signal_alpha_data_access.repositories import RawDetailRepository

        rows = await conn.fetch(
            "select id, ticker, name from stocks where ticker = any($1::text[]) order by ticker",
            TICKERS,
        )
        stocks = [(int(r["id"]), r["ticker"], r["name"]) for r in rows]
        if asof is None:
            asof = await conn.fetchval("select max(trade_date) from ohlcv_data")

        print(f"=== 결정론(수식) vs LLM 코호트 채점 — asof {asof} · model {model} ===")
        print(f"코호트: {', '.join(t for _, t, _ in stocks)} (한 번의 호출로 상대 채점)\n")

        repo = RawDetailRepository(conn)
        rule_scores: dict[str, dict[str, float | None]] = {t: {} for _, t, _ in stocks}
        llm_scores: dict[str, dict[str, object]] = {t: {} for _, t, _ in stocks}

        for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
            cfg = cfg_cls.from_env() if cfg_cls else None
            if kind == "price":
                loader = _PriceTrainingLoader(conn, window_days=3000)
            else:
                loader = _build_loader(
                    loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000), connection=conn
                )

            cohort: list[StockContext] = []
            for sid, ticker, name in stocks:
                evidence = await loader.load(stock_id=sid, stock_code=ticker, as_of=asof)
                raw = list(evidence[0].metadata.get("rows") or []) if evidence else []
                sector = (
                    evidence[0].metadata.get("sector_demand")
                    if (evidence and loader_key == "hiring") else None
                )
                pit = pit_rows(raw, asof, date_key=date_key)
                if not pit:
                    rule_scores[ticker][src] = None
                    llm_scores[ticker][src] = {"score": None}
                    continue
                rule_scores[ticker][src] = round(
                    float(await score_source(kind, pit, asof, cfg, sector, ind_fn, eval_fn)), 3
                )
                ev, hist = _evidence_for(src, pit, asof)
                cohort.append(
                    StockContext(
                        ticker=ticker,
                        name=name,
                        evidence=ev,
                        self_history=hist,
                        attention=_attention_for(pit, asof) if src == "DATALAB" else None,
                    )
                )

            if not cohort:
                print(f"  ▸ {src:8} 증거 0건 — 건너뜀")
                continue
            try:
                scored = await score_cohort(client, source=src, asof=str(asof), cohort=cohort)
            except Exception as exc:  # noqa: BLE001
                print(f"  ▸ {src:8} LLM 실패: {exc}")
                continue
            for s in scored:
                llm_scores[s.ticker][src] = {
                    "score": s.score,
                    "confidence": s.confidence,
                    "no_signal": s.no_signal,
                    "direction": s.direction,
                    "evidence": s.evidence,
                }
            tag = " ".join(
                f"{s.ticker}={'no_signal' if s.no_signal else f'{s.score:+.2f}'}" for s in scored
            )
            print(f"  ▸ {src:8} {tag}")

        # --- 비교표 ------------------------------------------------------------
        srcs = [s[0] for s in SOURCES]
        print("\n" + "=" * 92)
        print(f"{'종목':<12}{'소스':<10}{'수식':>10}{'LLM':>12}{'conf':>7}  근거")
        print("=" * 92)
        for _sid, ticker, name in stocks:
            for src in srcs:
                r = rule_scores[ticker].get(src)
                lo = llm_scores[ticker].get(src) or {}
                ls = lo.get("score")
                if r is None and ls is None:
                    continue
                r_txt = f"{r:+.3f}" if r is not None else "—"
                if lo.get("no_signal"):
                    l_txt, c_txt = "no_signal", ""
                elif ls is None:
                    l_txt, c_txt = "—", ""
                else:
                    l_txt = f"{ls:+.3f}"
                    c_txt = f"{lo.get('confidence', 0):.2f}"
                ev0 = (lo.get("evidence") or [""])[0][:44]
                print(f"{ticker:<12}{src:<10}{r_txt:>10}{l_txt:>12}{c_txt:>7}  {ev0}")
            print("-" * 92)

        # --- 등가중 블렌드 비교 ---------------------------------------------------
        print(f"\n{'종목':<14}{'수식 등가중':>14}{'LLM 등가중':>14}   차이")
        for _sid, ticker, name in stocks:
            rv = [v for v in rule_scores[ticker].values() if v is not None]
            lv = [
                float(o["score"])
                for o in llm_scores[ticker].values()
                if isinstance(o, dict) and o.get("score") is not None and not o.get("no_signal")
            ]
            rb = sum(rv) / len(rv) if rv else 0.0
            lb = sum(lv) / len(lv) if lv else 0.0
            print(f"{ticker + ' ' + name:<14}{rb:>+14.3f}{lb:>+14.3f}   {lb - rb:+.3f}")

        # --- DataLab 축 분리 검증 -------------------------------------------------
        print("\n[DataLab 축 분리 검증] attention(매그니튜드)이 방향 점수를 오염시켰는가?")
        for _sid, ticker, name in stocks:
            lo = llm_scores[ticker].get("DATALAB") or {}
            r = rule_scores[ticker].get("DATALAB")
            ls = "no_signal" if lo.get("no_signal") else lo.get("score")
            print(f"  {ticker}: 수식 DATALAB={r}  →  LLM DATALAB={ls}")
        print("  (수식은 검색 급증을 그대로 방향 점수로 바꿔 +0.8 을 냈다. LLM 이 이를 반복하면 실패.)")

        if out_json:
            Path(out_json).write_text(
                json.dumps(
                    {"asof": str(asof), "model": model, "rule": rule_scores, "llm": llm_scores},
                    ensure_ascii=False, indent=2, default=str,
                ),
                encoding="utf-8",
            )
            print(f"\nJSON → {out_json}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", type=date.fromisoformat, default=None)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--json", dest="out_json", default=None)
    args = ap.parse_args()
    asyncio.run(run(args.asof, args.model, args.out_json))


if __name__ == "__main__":
    main()
