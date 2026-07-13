"""3종목 소스별 점수 산출 (기준선 스냅샷).

LLM 채점기로 교체하기 **전**의 결정론 점수를 찍어 둔다. 교체 후 같은 (종목, asof) 에서
LLM 점수를 뽑아 나란히 비교하기 위한 before-picture.

로컬 DataLab 매핑이 있는 3종목(삼성전자·SK하이닉스·NAVER)이 대상. 경로는 프로덕션과 동일:
  evidence loader → pit_rows(known_at <= asof, 미래행 차단) → 결정론 채점기(reference_scorer)

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    python scripts/score_three_stocks.py                # 최신 asof
    python scripts/score_three_stocks.py --asof 2026-05-01 --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))

import asyncpg  # noqa: E402

from app.analyzers.config import AggregatorConfig  # noqa: E402
from app.backtest.reference_scorer import SOURCES, score_source  # noqa: E402
from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TICKERS = ["005930", "000660", "035420"]


def _require_local(db_url: str) -> None:
    if not any(host in db_url for host in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


def _direction(score: float, cfg: AggregatorConfig) -> str:
    if score >= cfg.positive_threshold:
        return "positive"
    if score <= cfg.negative_threshold:
        return "negative"
    return "neutral"


async def run(asof: date | None, out_json: str | None) -> None:
    db_url = os.environ["DATABASE_URL"]
    _require_local(db_url)
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
        print(f"=== 결정론(수식) 소스 점수 — asof {asof} ===")
        print("score 는 signed [-1, +1]. 방향 임계 ±0.2 (AggregatorConfig).\n")

        agg_cfg = AggregatorConfig.from_env()
        repo = RawDetailRepository(conn)
        results: dict[str, dict[str, dict]] = {t: {} for _, t, _ in stocks}

        for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
            cfg = cfg_cls.from_env() if cfg_cls else None
            if kind == "price":
                loader = _PriceTrainingLoader(conn, window_days=3000)
            else:
                loader = _build_loader(
                    loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000), connection=conn
                )
            for sid, ticker, _name in stocks:
                evidence = await loader.load(stock_id=sid, stock_code=ticker, as_of=asof)
                raw = list(evidence[0].metadata.get("rows") or []) if evidence else []
                sector = evidence[0].metadata.get("sector_demand") if (evidence and loader_key == "hiring") else None
                pit = pit_rows(raw, asof, date_key=date_key)
                if not pit:
                    results[ticker][src] = {"score": None, "rows": 0, "status": "no_data"}
                    continue
                score = await score_source(kind, pit, asof, cfg, sector, ind_fn, eval_fn)
                results[ticker][src] = {
                    "score": round(float(score), 3),
                    "rows": len(pit),
                    "status": "ok",
                }

        # --- 표 출력 -----------------------------------------------------------
        srcs = [s[0] for s in SOURCES]
        header = f"{'종목':<12}" + "".join(f"{s:>12}" for s in srcs) + f"{'등가중평균':>14}{'방향':>10}"
        print(header)
        print("-" * 100)
        for sid, ticker, name in stocks:
            cells = ""
            scoring = []
            for src in srcs:
                r = results[ticker][src]
                if r["score"] is None:
                    cells += f"{'—':>12}"
                else:
                    cells += f"{r['score']:>+12.3f}"
                    scoring.append(r["score"])
            blend = sum(scoring) / len(scoring) if scoring else 0.0
            label = f"{ticker} {name}"
            print(f"{label:<12}{cells}{blend:>+14.3f}{_direction(blend, agg_cfg):>10}")

        print("\n행 수(PIT 통과한 증거 건수):")
        for sid, ticker, name in stocks:
            detail = ", ".join(
                f"{src}={results[ticker][src]['rows']}" for src in srcs
            )
            print(f"  {ticker} {name:<10} {detail}")
        print("\n※ '—' = 해당 시점에 그 소스의 증거가 0건(no_data) → 등가중 평균에서 제외됨")

        if out_json:
            Path(out_json).write_text(
                json.dumps({"asof": str(asof), "results": results}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\nJSON → {out_json}")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", type=date.fromisoformat, default=None)
    ap.add_argument("--json", dest="out_json", default=None)
    args = ap.parse_args()
    asyncio.run(run(args.asof, args.out_json))


if __name__ == "__main__":
    main()
