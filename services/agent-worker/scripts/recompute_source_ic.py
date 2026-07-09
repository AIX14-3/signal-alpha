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
from types import SimpleNamespace

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # agent-worker 루트 (app.* 임포트)
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))  # signal_alpha_data_access
sys.path.insert(0, str(_HERE.parent))  # scripts (ic_diagnostic 재사용)

import asyncpg  # noqa: E402

from app.analyzers.config import (  # noqa: E402
    DartRuleConfig,
    DataLabRuleConfig,
    HiringRuleConfig,
    PatentRuleConfig,
)
from app.analyzers.dart.source_result import build_dart_analysis_result  # noqa: E402
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators  # noqa: E402
from app.analyzers.datalab.rules import evaluate_indicators as datalab_eval  # noqa: E402
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators  # noqa: E402
from app.analyzers.hiring.rules import evaluate_indicators as hiring_eval  # noqa: E402
from app.analyzers.patent.indicators import compute_indicators as patent_indicators  # noqa: E402
from app.analyzers.patent.rules import evaluate_indicators as patent_eval  # noqa: E402
from app.analyzers.price.analyzer import PriceAnalyzer  # noqa: E402
from app.ml.source_features import KNOWN_AT, pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402
from ic_diagnostic import _fmt, _metrics, suggest_weights  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TARGET = "fwd_return_20d"
DEFAULT_UNIVERSE = "kospi20_seed"

# (SRC, kind, loader_key, date_key, indicators_fn, eval_fn, config_cls). kind:
#   rules = compute_indicators + evaluate_indicators (대체데이터 3소스)
#   dart  = build_dart_analysis_result(events).score (결정론 DART 점수)
#   price = PriceAnalyzer().analyze (⚠️ 자기참조: 과거가격→미래가격, 대체데이터 알파 아님)
SOURCES = [
    ("PATENT", "rules", "patent", KNOWN_AT["patent"], patent_indicators, patent_eval, PatentRuleConfig),
    ("DATALAB", "rules", "datalab", KNOWN_AT["datalab"], datalab_indicators, datalab_eval, DataLabRuleConfig),
    ("HIRING", "rules", "hiring", KNOWN_AT["hiring"], hiring_indicators, hiring_eval, HiringRuleConfig),
    ("DART", "dart", "dart", KNOWN_AT["dart"], None, None, DartRuleConfig),
    ("PRICE", "price", "price", KNOWN_AT["price"], None, None, None),
]
# REPORT 제외: 결정론 분석기/로더 경로가 없고(밸류에이션 별도 경로) 로컬 데이터 27건·최근이라
# 20일 선행수익률 미경과 → 측정 불가. 데이터·경로 확보 시 별도.


async def _score(kind, pit, asof, cfg, sector, ind_fn, eval_fn) -> float:
    """소스 kind 별 점수 재계산(누수차단된 pit 행 입력). signed [-1, +1] 반환."""
    if kind == "dart":
        return build_dart_analysis_result(pit).score
    if kind == "price":
        res = await PriceAnalyzer().analyze("", [SimpleNamespace(metadata={"rows": pit})])
        return res.score
    # rules (patent/datalab/hiring)
    lookback = getattr(cfg, "lookback_days", 30)
    if ind_fn is hiring_indicators:
        ind = ind_fn(pit, as_of=asof, lookback_days=lookback, sector_demand=sector)
    else:
        ind = ind_fn(pit, as_of=asof, lookback_days=lookback)
    return eval_fn(ind, cfg).score


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
    for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
        cfg = cfg_cls.from_env() if cfg_cls else None
        # 종목당 전 이력을 1회만 로드(as_of=max_asof, 큰 창) → 메모리에서 asof 별 pit 필터.
        # (stock,asof) 마다 DB 재조회하던 O(라벨수) 쿼리를 O(종목수)로 줄인다. 누수는 pit_rows 차단.
        if kind == "price":
            loader = _PriceTrainingLoader(conn, window_days=3000)
        else:
            loader = _build_loader(loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000), connection=conn)
        rows_by_stock: dict[int, list] = {}
        sector_by_stock: dict[int, object] = {}
        for sid in stock_ids:
            ev = await loader.load(stock_id=sid, stock_code="", as_of=max_asof)
            rows_by_stock[sid] = list(ev[0].metadata.get("rows") or []) if ev else []
            if loader_key == "hiring" and ev:
                sector_by_stock[sid] = ev[0].metadata.get("sector_demand")

        pairs: list[tuple[float, float]] = []
        for row in labeled:
            sid = int(row["stock_id"])
            asof = row["asof_date"]
            pit = pit_rows(rows_by_stock.get(sid) or [], asof, date_key=date_key)
            if not pit:
                continue  # 소스가 그 시점에 침묵(행 0)
            score = await _score(kind, pit, asof, cfg, sector_by_stock.get(sid), ind_fn, eval_fn)
            # 0-100 스케일 변환(ic_diagnostic._metrics/_hit_rate 는 중심 50 가정). IC 는 스케일 불변.
            pairs.append((score * 50.0 + 50.0, float(row[TARGET])))

        src_res[src] = _metrics(pairs)
        m = src_res[src]
        note = " (⚠️자기참조)" if src == "PRICE" else ""
        print(f"  ▸ {src:8} N={m['n']:5}  IC {_fmt(m['ic'])}  hit {_fmt(m['hit_rate'], True)}  "
              f"perm-p {m['perm_p']}{note}")
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
    print(f"recompute-IC — 재계산 표본 라벨 {results['labels']}건 · 20일 지평선 · 5소스(대체3+DART+주가, REPORT 제외)")
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
