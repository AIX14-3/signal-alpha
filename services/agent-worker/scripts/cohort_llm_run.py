"""3종목 × 6소스 × N일 LLM 코호트 채점 + 에피소드 메모리(임베딩) 검증.

앞선 스크립트(score_three_stocks_llm.py)의 한계 3가지를 메운다:
  1. REPORT 누락 — SOURCES 에 REPORT 가 없었다(로더 경로 부재). 여기서 붙인다.
  2. 단일 asof — 하루치만 봤다. 여기선 최근 N 영업일을 훑는다.
  3. 메모리 미검증 — 채점표를 없애면 '점수 표류'가 최대 리스크다. 에피소드 메모리(pgvector)를
     **표류 탐지기**로 쓸 수 있는지 실제로 확인한다.

## 메모리 임베딩의 용도 (중요)
방향 알파를 만드는 데는 못 쓴다(임베딩 KNN 방향예측 파일럿은 이미 무신호로 판명).
여기서 쓰는 용도는 **일관성 검수**다: "과거의 비슷한 상황에서 LLM 이 비슷한 점수를 줬는가?"
- 상황 텍스트는 프로덕션 렌더러(``app/memory/situation.build_situation_text``)를 그대로 쓴다.
- 새 점수를 낼 때 과거 유사 에피소드 K개를 코사인 검색해 점수 편차를 본다.
- 편차가 크면 표류 후보 → ``needs_review``.

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    export GEMINI_API_KEY=...  EMBEDDING_MODEL=gemini-embedding-001
    python scripts/cohort_llm_run.py --days 7 --model gemini-2.5-flash
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
from app.analyzers.llm_aggregator import aggregate_cohort, blend_basis  # noqa: E402
from app.analyzers.llm_scorer import (  # noqa: E402
    SOURCE_GUIDES,
    StockContext,
    StockScore,
    score_cohort,
)
from app.backtest.reference_scorer import SOURCES, score_source  # noqa: E402
from app.clients.embedding_client import GeminiEmbeddingClient  # noqa: E402
from app.clients.gemini_client import GeminiJsonClient  # noqa: E402
from app.clients.vertex_client import VertexEmbeddingClient, VertexJsonClient  # noqa: E402
from app.memory.situation import SituationInput, build_situation_text  # noqa: E402
from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TICKERS = ["005930", "000660", "035420"]
MAX_ITEMS = 40
RUN_KEY = "LLM_COHORT"


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


def _d(v) -> str:
    return str(v)[:10]


def _monthly_avg(series: list[tuple[str, float]]) -> list[dict]:
    buckets: dict[str, list[float]] = {}
    for day, value in series:
        buckets.setdefault(day[:7], []).append(value)
    return [{"month": m, "avg": round(sum(v) / len(v), 2)} for m, v in sorted(buckets.items())]


# --- 정규화 증거 압축 ---------------------------------------------------------------
# 원칙: "코드는 세고, LLM 은 판단한다."
#   준다   → 시계열·목록 그 자체 + **산술 집계**(카운트·합계·월별 버킷)
#   안 준다 → 사람이 정한 **판단성 파생비율**(momentum_pct·spike_ratio·net_polarity)
#            그리고 데이터 적재량 아티팩트(행 개수 같은 것 — LLM 이 가짜 신호로 오용한다)
def _evidence_for(src: str, pit: list[dict], close: float | None) -> tuple[dict, dict]:
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

        def _sd(row) -> float:
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


def _attention(pit: list[dict], asof: date) -> dict | None:
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


async def _report_rows(conn, stock_id: int, asof: date) -> list[dict]:
    """report_valuation_facts 직접 조회 (report evidence loader 가 아직 없다)."""
    rows = await conn.fetch(
        "select publish_date, target_price, broker from report_valuation_facts "
        "where stock_id=$1 and target_price is not null order by publish_date",
        stock_id,
    )
    return [dict(r) for r in rows]


def _abort_if_quota(exc: Exception, asof, where: str) -> None:
    """쿼터 소진이면 **즉시 중단**. 계속 돌면 모든 소스가 조용히 '실패'로 떨어져
    **스키마 위반처럼 보이는 오염된 결과**가 나온다(실제로 한 번 그렇게 오독했다).

    단, 429 를 두 종류로 구분한다:
      - AI Studio "prepayment credits depleted" → **회복 불가**. 충전 전엔 못 돈다.
      - Vertex "Resource exhausted"            → **일시적 공유 용량 스로틀**. 클라이언트가
        지수 백오프로 재시도하며, 그걸 다 소진하고도 실패했다면 그때만 여기 도달한다.
    """
    text = str(exc)
    if "429" not in text:
        return
    depleted = "credits are depleted" in text
    kind = "선불 크레딧 소진(회복 불가 — 충전 필요)" if depleted else \
        "용량 스로틀(백오프 재시도까지 모두 소진)"
    raise SystemExit(
        f"\n❌ [{asof}] {where}: HTTP 429 — {kind}. 실행 중단.\n"
        f"   부분 결과는 신뢰하지 말 것(모든 소스가 실패로 떨어져 스키마 위반처럼 보인다).\n"
        f"   원문: {text[:300]}"
    ) from exc


def _build_client(provider: str, model: str):
    """프로바이더 중립 — 채점기는 JsonLlm 프로토콜만 본다.

    vertex : GCP 결제계좌 직결(ADC). AI Studio 선불 크레딧과 무관 → 크레딧 소진으로 안 멈춘다.
             finishReason/maxOutputTokens/thinking 을 제대로 다뤄 스키마 위반의 주원인을 없앤다.
    aistudio: 기존 GeminiJsonClient(선불 크레딧 경로). 비교용으로 남긴다.
    """
    if provider == "vertex":
        return VertexJsonClient(model=model, temperature=0.0)
    if provider == "aistudio":
        return GeminiJsonClient(model=model, temperature=0.0)
    raise SystemExit(f"unknown provider: {provider}")


async def run(
    days: int, model: str, out_json: str | None, do_memory: bool, provider: str = "vertex"
) -> None:
    db_url = os.environ["DATABASE_URL"]
    _require_local(db_url)
    client = _build_client(provider, model)

    conn = await asyncpg.connect(db_url)
    try:
        from signal_alpha_data_access.repositories import RawDetailRepository

        rows = await conn.fetch(
            "select id, ticker, name from stocks where ticker = any($1::text[]) order by ticker",
            TICKERS,
        )
        stocks = [(int(r["id"]), r["ticker"], r["name"]) for r in rows]
        asofs = [
            r["trade_date"]
            for r in await conn.fetch(
                "select distinct trade_date from ohlcv_data order by trade_date desc limit $1", days
            )
        ][::-1]

        print(f"=== 3종목 × 6소스 × {len(asofs)}일 — {provider}/{model} ===")
        print(f"asof: {asofs[0]} ~ {asofs[-1]}\n")

        repo = RawDetailRepository(conn)
        # asof 별 · 소스별 결과
        out: dict[str, dict[str, dict[str, dict]]] = {}
        calls = 0
        schema_fails = 0
        tok_in = tok_out = tok_think = 0

        for asof in asofs:
            closes = {
                t: float(
                    await conn.fetchval(
                        "select close from ohlcv_data o join stocks s on s.id=o.stock_id "
                        "where s.ticker=$1 and o.trade_date<=$2 order by o.trade_date desc limit 1",
                        t, asof,
                    )
                )
                for _s, t, _n in stocks
            }
            day: dict[str, dict[str, dict]] = {t: {} for _s, t, _n in stocks}
            per_stock_scores: dict[str, dict[str, StockScore]] = {}

            for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
                if src not in SOURCE_GUIDES:
                    continue
                cfg = cfg_cls.from_env() if cfg_cls else None
                cohort: list[StockContext] = []

                for sid, ticker, name in stocks:
                    if src == "REPORT":
                        raw = await _report_rows(conn, sid, asof)
                    elif kind == "price":
                        ev_rows = await _PriceTrainingLoader(conn, window_days=3000).load(
                            stock_id=sid, stock_code=ticker, as_of=asof)
                        raw = list(ev_rows[0].metadata.get("rows") or []) if ev_rows else []
                    else:
                        loader = _build_loader(
                            loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000),
                            connection=conn)
                        ev_rows = await loader.load(stock_id=sid, stock_code=ticker, as_of=asof)
                        raw = list(ev_rows[0].metadata.get("rows") or []) if ev_rows else []

                    pit = pit_rows(raw, asof, date_key=date_key)
                    if not pit:
                        day[ticker][src] = {"rule": None, "llm": None, "status": "no_data"}
                        continue
                    rule = round(float(await score_source(
                        kind, pit, asof, cfg, None, ind_fn, eval_fn, current_close=closes[ticker]
                    )), 3)
                    day[ticker][src] = {"rule": rule, "llm": None, "rows": len(pit)}
                    ev, hist = _evidence_for(src, pit, closes[ticker])
                    cohort.append(StockContext(
                        ticker=ticker, name=name, evidence=ev, self_history=hist,
                        attention=_attention(pit, asof) if src == "DATALAB" else None,
                    ))

                if not cohort:
                    continue
                try:
                    scored = await score_cohort(client, source=src, asof=str(asof), cohort=cohort)
                    calls += 1
                    u = getattr(client, "last_usage", {}) or {}
                    tok_in += u.get("input_tokens", 0)
                    tok_out += u.get("output_tokens", 0)
                    tok_think += u.get("thought_tokens", 0)
                except Exception as exc:  # noqa: BLE001
                    _abort_if_quota(exc, asof, src)
                    print(f"  [{asof}] {src}: 스키마 위반 — {exc}")
                    schema_fails += 1
                    continue
                for s in scored:
                    day[s.ticker][src].update({
                        "llm": None if s.no_signal else s.score,
                        "no_signal": s.no_signal,
                        "confidence": s.confidence,
                        "direction": s.direction,
                        "evidence": s.evidence,
                    })
                    per_stock_scores.setdefault(s.ticker, {})[src] = s

            # --- LLM 통합 판정 (등가중 평균이 아니다) ---
            if per_stock_scores:
                try:
                    verdicts = await aggregate_cohort(
                        client, asof=str(asof), per_stock=per_stock_scores,
                        names={t: n for _s, t, n in stocks},
                    )
                    calls += 1
                    u = getattr(client, "last_usage", {}) or {}
                    tok_in += u.get("input_tokens", 0)
                    tok_out += u.get("output_tokens", 0)
                    tok_think += u.get("thought_tokens", 0)
                    for v in verdicts:
                        day[v.ticker]["_verdict"] = {
                            "final_score": v.final_score,
                            "score_100": v.score_100,
                            "signal": v.signal,
                            "confidence": v.confidence,
                            "conflict": v.conflict,
                            "headline": v.headline,
                            "positive_evidence": v.positive_evidence,
                            "caution_evidence": v.caution_evidence,
                            "consensus_score": v.consensus_score,
                            "source_agreement": v.source_agreement,
                            "blend_basis": blend_basis(v),
                        }
                except Exception as exc:  # noqa: BLE001
                    if "429" in str(exc):
                        raise SystemExit(f"\n❌ API 쿼터/크레딧 소진 (429) — 중단. {exc}") from exc
                    print(f"  [{asof}] AGGREGATE 스키마 위반 — {exc}")
                    schema_fails += 1
            out[str(asof)] = day
            line = " | ".join(
                f"{t} {(day[t].get('_verdict') or {}).get('final_score', 0.0):+.2f}"
                f"({(day[t].get('_verdict') or {}).get('signal', '-')})"
                for _s, t, _n in stocks
            )
            print(f"[{asof}] 통합 → {line}")

        total = calls + schema_fails
        rate = (schema_fails / total * 100) if total else 0.0
        print(f"\nLLM 호출: 성공 {calls}회 / 스키마 위반 {schema_fails}회 "
              f"(위반율 {rate:.1f}%) — 위반율은 모델 선정 1순위 지표")
        if tok_in or tok_out:
            # gemini-2.5-flash 공시 단가(2026-07): 입력 $0.30 / 출력 $2.50 per 1M.
            cost = tok_in / 1e6 * 0.30 + tok_out / 1e6 * 2.50
            per_day = cost / max(1, len(asofs))
            print(f"토큰 실측: 입력 {tok_in:,} / 출력 {tok_out:,} / thinking {tok_think:,}")
            print(f"비용: ${cost:.4f} ({len(asofs)}일·3종목) → 1일 3종목 ${per_day:.4f}")
            print(f"  ↳ 200종목 환산(코호트 10종목 가정): 1일 약 ${per_day / 3 * 200 / 10 * 3:.2f} "
                  f"· 월 약 ${per_day / 3 * 200 / 10 * 3 * 30:.0f}")
        _print_matrix(out, asofs, stocks)

        if do_memory:
            await _memory_check(conn, out, asofs, stocks, provider)

        if out_json:
            Path(out_json).write_text(
                json.dumps({"model": model, "days": days, "results": out},
                           ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
            print(f"\nJSON → {out_json}")
    finally:
        await conn.close()


def _print_matrix(out, asofs, stocks) -> None:
    srcs = [s for s in SOURCE_GUIDES]
    print("\n" + "=" * 100)
    print("소스 커버리지 · 수식 vs LLM (마지막 asof 기준)")
    print("=" * 100)
    last = str(asofs[-1])
    print(f"{'종목':<12}{'소스':<9}{'행수':>7}{'수식':>9}{'LLM':>11}{'conf':>7}  근거")
    for _sid, t, name in stocks:
        for src in srcs:
            e = out[last][t].get(src)
            if not e or src == "_verdict":
                continue
            rows = e.get("rows", 0)
            rule = f"{e['rule']:+.3f}" if e.get("rule") is not None else "—"
            if e.get("status") == "no_data":
                llm, conf = "no_data", ""
            elif e.get("no_signal"):
                llm, conf = "no_signal", ""
            elif e.get("llm") is None:
                llm, conf = "—", ""
            else:
                llm, conf = f"{e['llm']:+.3f}", f"{e.get('confidence', 0):.2f}"
            ev0 = (e.get("evidence") or [""])[0][:38]
            print(f"{t:<12}{src:<9}{rows:>7}{rule:>9}{llm:>11}{conf:>7}  {ev0}")
        print("-" * 100)

    # --- LLM 통합 판정 (aggregator) ---
    print("\n" + "=" * 100)
    print(f"LLM 통합 판정 (aggregator) — asof {last}")
    print("=" * 100)
    for _sid, t, name in stocks:
        v = out[last][t].get("_verdict")
        if not v:
            print(f"  {t} {name}: 통합 실패")
            continue
        print(f"\n  ▪ {t} {name}  →  {v['final_score']:+.3f}  (0-100: {v['score_100']})  "
              f"{v['signal'].upper()}  conf {v['confidence']:.2f}"
              f"{'  ⚠️충돌' if v['conflict'] else ''}")
        print(f"    합의도 {v['consensus_score']:.0f}% ({v['source_agreement']})")
        print(f"    헤드라인: {v['headline']}")
        for e in v["positive_evidence"][:3]:
            print(f"      + {e[:80]}")
        for e in v["caution_evidence"][:3]:
            print(f"      ⚠ {e[:80]}")
        w = ", ".join(f"{c['source']} {c['weight']:.0%}" for c in v["blend_basis"]["sources"])
        print(f"    반영 가중(LLM 자기판단): {w}")

    print("\n일자별 LLM 통합 점수 (안정성 — 하루하루 튀면 표류다)")
    hdr = f"{'asof':<12}" + "".join(f"{t:>12}" for _s, t, _n in stocks)
    print(hdr)
    for a in asofs:
        row = f"{str(a):<12}"
        for _s, t, _n in stocks:
            v = out[str(a)][t].get("_verdict") or {}
            row += f"{v.get('final_score', 0.0):>+12.3f}"
        print(row)


async def _memory_check(conn, out, asofs, stocks, provider: str = "vertex") -> None:
    """에피소드 메모리(pgvector) 표류 탐지기로서 실제 작동하는지 확인."""
    print("\n" + "=" * 100)
    print("에피소드 메모리 검증 — 임베딩 저장 → 코사인 KNN 회상 → 점수 일관성")
    print("=" * 100)
    # 임베딩도 프로바이더를 맞춰야 한다 — 채점만 Vertex 로 넘기고 임베딩을 AI Studio 로
    # 두면 크레딧이 마르는 순간 여기서 터진다(실제로 그랬다).
    embedder = VertexEmbeddingClient() if provider == "vertex" else GeminiEmbeddingClient()
    print(f"embedding model: {embedder.model}")

    stored = 0
    vectors: dict[tuple[str, str], list[float]] = {}
    for a in asofs:
        for sid, t, _n in stocks:
            day = out[str(a)][t]
            breakdown = {
                src: {
                    "direction": e.get("direction", "neutral"),
                    "score": e.get("llm") if e.get("llm") is not None else 0.0,
                    "data_status": "no_signal" if e.get("no_signal") else (
                        "missing" if e.get("status") == "no_data" else "ok"),
                    "summary": (e.get("evidence") or [""])[0][:80],
                }
                for src, e in day.items()
                if src != "_verdict"
            }
            # 에피소드의 점수/방향은 **LLM aggregator 의 통합 판정**을 쓴다(임시 평균이 아니라).
            verdict = day.get("_verdict") or {}
            blend = float(verdict.get("final_score", 0.0))
            signal = str(verdict.get("signal", "neutral"))
            text = build_situation_text(SituationInput(signal=signal, breakdown=breakdown))
            vec = await embedder.embed(text)
            vectors[(str(a), t)] = vec
            await conn.execute(
                "insert into signal_episodes (stock_id, signal_date, run_key, direction, score, "
                "sources, embedding) values ($1,$2,$3,$4,$5,$6::jsonb,$7::vector) "
                "on conflict (stock_id, signal_date, run_key) do update set "
                "direction=excluded.direction, score=excluded.score, embedding=excluded.embedding",
                sid, a, RUN_KEY, signal, blend,
                json.dumps(
                    {k: v.get("llm") for k, v in day.items() if k != "_verdict"}, default=str
                ),
                "[" + ",".join(f"{x:.6f}" for x in vec) + "]",
            )
            stored += 1
    print(f"저장된 에피소드: {stored}건 (3종목 × {len(asofs)}일)")

    # --- KNN 회상: 마지막 asof 의 상황과 가장 가까운 **과거** 에피소드 ---
    print("\n[KNN 회상] 마지막 asof 상황 → 가장 유사한 과거 에피소드 3건")
    last = asofs[-1]
    drift_flags = 0
    for sid, t, name in stocks:
        vec = vectors[(str(last), t)]
        lit = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
        rows = await conn.fetch(
            "select s.ticker, e.signal_date, e.score, e.direction, "
            "       1 - (e.embedding <=> $1::vector) as sim "
            "from signal_episodes e join stocks s on s.id = e.stock_id "
            "where e.run_key=$2 and e.signal_date < $3 "
            "order by e.embedding <=> $1::vector limit 3",
            lit, RUN_KEY, last,
        )
        cur = next((r["score"] for r in await conn.fetch(
            "select score from signal_episodes where stock_id=$1 and signal_date=$2 and run_key=$3",
            sid, last, RUN_KEY)), 0.0)
        print(f"\n  {t} {name} — 현재 점수 {cur:+.3f}")
        if not rows:
            print("    (과거 에피소드 없음)")
            continue
        for r in rows:
            gap = abs(float(r["score"]) - float(cur))
            mark = "  ⚠️ 편차 큼" if gap > 0.3 else ""
            print(f"    유사도 {r['sim']:.3f}  {r['ticker']} {r['signal_date']}  "
                  f"점수 {r['score']:+.3f}  (편차 {gap:.3f}){mark}")
            if gap > 0.3:
                drift_flags += 1

    print(f"\n표류 후보(유사 상황인데 점수 편차 > 0.3): {drift_flags}건")
    print("→ 임베딩 회상이 동작하면 이 편차가 needs_review 트리거로 쓸 수 있다.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--provider", default="vertex", choices=["vertex", "aistudio"])
    ap.add_argument("--json", dest="out_json", default=None)
    ap.add_argument("--no-memory", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.days, args.model, args.out_json, not args.no_memory, args.provider))


if __name__ == "__main__":
    main()
