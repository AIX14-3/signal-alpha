"""recompute-IC: 과거 시점(event_study_panel asof)에 소스별 점수를 **재계산**해 이미 있는
선행수익률 라벨과 잇고, 소스별 IC 를 실측한다.

왜 재계산인가: 저장된 final_signals 는 전부 최근 신호라 20일 선행수익률 구간이 아직 안 지나
(미래 가격 없음) IC 측정 불가. 반면 event_study_panel 은 과거 asof + forward return 라벨을
이미 갖고 있으므로, 그 과거 시점에 원본에서 소스 점수를 재계산하면 실제 IC 를 잴 수 있다.

경로(프로덕션과 동일 + 누수차단):
  라벨(EventStudyRepository) → 각 (stock, asof) 에서 소스 evidence 로더로 원본 로드 →
  pit_rows(known_at ≤ asof, 미래행 차단) → compute_indicators → evaluate_indicators → score →
  (score, fwd_return_20d) 쌍 → 소스별 IC/hit/순열검정 → 권장 ALT_WEIGHT (ic_diagnostic 재사용).

스코프: 대체데이터 3소스(PATENT·DATALAB·HIRING). PRICE/DART/REPORT 제외.

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    python scripts/recompute_source_ic.py
    python scripts/recompute_source_ic.py --asof-from 2021-01-01 --asof-to 2026-06-01 --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # agent-worker 루트 (app.* 임포트)
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))  # signal_alpha_data_access
sys.path.insert(0, str(_HERE.parent))  # scripts (ic_diagnostic 재사용)

import asyncpg  # noqa: E402

from app.analyzers.config import (  # noqa: E402
    DataLabRuleConfig,
    HiringRuleConfig,
    PatentRuleConfig,
)
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators  # noqa: E402
from app.analyzers.datalab.rules import evaluate_indicators as datalab_eval  # noqa: E402
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators  # noqa: E402
from app.analyzers.hiring.rules import evaluate_indicators as hiring_eval  # noqa: E402
from app.analyzers.patent.indicators import compute_indicators as patent_indicators  # noqa: E402
from app.analyzers.patent.rules import evaluate_indicators as patent_eval  # noqa: E402
from app.ml.source_features import KNOWN_AT, pit_rows  # noqa: E402
from app.ml.train_source_models import _build_loader  # noqa: E402
from ic_diagnostic import _fmt, _metrics, suggest_weights  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TARGET = "fwd_return_20d"
DEFAULT_UNIVERSE = "kospi20_seed"

# SRC(대문자, ALT_WEIGHT/SCORING_SOURCES 정합) → (loader source key, indicators, rules, config)
SOURCES = {
    "PATENT": ("patent", patent_indicators, patent_eval, PatentRuleConfig),
    "DATALAB": ("datalab", datalab_indicators, datalab_eval, DataLabRuleConfig),
    "HIRING": ("hiring", hiring_indicators, hiring_eval, HiringRuleConfig),
}


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


async def recompute(conn, *, asof_from, asof_to, universe) -> dict:
    from signal_alpha_data_access.repositories import EventStudyRepository, RawDetailRepository

    labels = await EventStudyRepository(conn).list_for_training(
        asof_from=asof_from, asof_to=asof_to, universe_snapshot=universe
    )
    labeled = [row for row in labels if row[TARGET] is not None]
    stock_ids = sorted({int(row["stock_id"]) for row in labeled})
    max_asof = max(row["asof_date"] for row in labeled)
    print(f"라벨 {len(labels)}건 중 {TARGET} 유효 {len(labeled)}건 · 종목 {len(stock_ids)}개 — 재계산 시작")

    repo = RawDetailRepository(conn)
    src_res: dict[str, dict] = {}
    for src, (source_key, indicators_fn, eval_fn, cfg_cls) in SOURCES.items():
        cfg = cfg_cls.from_env()
        # 종목당 전 이력을 1회만 로드(as_of=max_asof, lookback 크게) → 메모리에서 asof 별 pit 필터.
        # (stock,asof) 마다 DB 재조회하던 O(라벨수) 쿼리를 O(종목수)로 줄인다. 누수는 pit_rows 가 차단.
        loader = _build_loader(source_key, repo, max(cfg.lookback_days, 3000), connection=conn)
        rows_by_stock: dict[int, list] = {}
        sector_by_stock: dict[int, object] = {}
        for sid in stock_ids:
            ev = await loader.load(stock_id=sid, stock_code="", as_of=max_asof)
            rows_by_stock[sid] = list(ev[0].metadata.get("rows") or []) if ev else []
            if source_key == "hiring" and ev:
                sector_by_stock[sid] = ev[0].metadata.get("sector_demand")

        pairs: list[tuple[float, float]] = []
        for row in labeled:
            sid = int(row["stock_id"])
            asof = row["asof_date"]
            pit = pit_rows(rows_by_stock.get(sid) or [], asof, date_key=KNOWN_AT[source_key])
            if not pit:
                continue  # 소스가 그 시점에 침묵(행 0)
            if source_key == "hiring":
                ind = indicators_fn(
                    pit, as_of=asof, lookback_days=cfg.lookback_days,
                    sector_demand=sector_by_stock.get(sid),
                )
            else:
                ind = indicators_fn(pit, as_of=asof, lookback_days=cfg.lookback_days)
            # 0-100 스케일 변환(ic_diagnostic._metrics/_hit_rate 는 중심 50 가정). IC 는 스케일 불변.
            pairs.append((eval_fn(ind, cfg).score * 50.0 + 50.0, float(row[TARGET])))

        src_res[src] = _metrics(pairs)
        m = src_res[src]
        print(f"  ▸ {src:8} N={m['n']:5}  IC {_fmt(m['ic'])}  hit {_fmt(m['hit_rate'], True)}  "
              f"perm-p {m['perm_p']}")
    return {"labels": len(labeled), "source_ic_20d": src_res}


async def main(json_out: str | None, asof_from: date, asof_to: date, universe: str | None) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            results = await recompute(conn, asof_from=asof_from, asof_to=asof_to, universe=universe)
    finally:
        await pool.close()

    print("\n" + "=" * 76)
    print(f"recompute-IC — 재계산 표본 라벨 {results['labels']}건 · 20일 지평선 · 대체데이터 3소스")
    print("=" * 76)
    suggestion = suggest_weights(results["source_ic_20d"], include_price=False)
    results["suggested"] = suggestion
    print(f"권장: weight_mode = {suggestion['mode']}  ({suggestion['rationale']})")
    if suggestion["mode"] == "ic":
        print("  → 검토 후 env 적용:")
        print("     ALT_WEIGHT_MODE=ic")
        for src, w in suggestion["weights"].items():
            print(f"     ALT_WEIGHT_{src}={w}")
    print("해석: IC 부호=재계산 점수와 선행수익률 상관(양수=예측적). 대체데이터 방향알파는")
    print("      선례상 대체로 null — null 이면 등가중 유지가 정답(가중=노이즈 과적합 방지).")

    if json_out:
        import json
        Path(json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON 요약 → {json_out}")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof-from", type=_d, default=_d("2021-01-01"))
    ap.add_argument("--asof-to", type=_d, default=_d("2026-06-01"))
    ap.add_argument("--universe", default=DEFAULT_UNIVERSE, help="event_study_panel universe_snapshot (기본 kospi20_seed)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    asyncio.run(main(args.json, args.asof_from, args.asof_to, args.universe))
