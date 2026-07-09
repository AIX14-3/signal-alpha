"""어그리게이터 방향 컷오프(±θ) 독립 검증 — 과거 재구성 등가중 통합점수 기반.

팀원 하네스(final_signals 512건·DART단독)를 독립 재현: 여기선 recompute 로 과거 asof 마다
소스별 점수를 재계산·등가중 평균해 통합점수를 만들고, θ를 sweep해 시장중립(날짜-demean=abn)
선행수익률의 positive/negative 버킷 스프레드·t-stat 을 본다. 표본이 훨씬 크다(7k+).

look-ahead 0(pit_rows) · abn(같은 asof 평균차감) · 게이트 n±≥MIN_N. LOCAL DB only.
Run: python scripts/aggregate_threshold_sweep.py --horizon 5   (또는 20)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))
sys.path.insert(0, str(_HERE.parent))

import asyncpg  # noqa: E402

from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402
from ic_diagnostic import MIN_N, _pearson  # noqa: E402
from recompute_source_ic import SOURCES, _demean, _score  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

THETAS = [round(0.02 * i, 2) for i in range(0, 26)]  # 0.00 .. 0.50


def _require_local(db: str) -> None:
    if "localhost" not in db and "127.0.0.1" not in db:
        raise SystemExit("LOCAL only")


async def build_aggregate(conn, *, asof_from, asof_to, universe, target) -> list:
    from signal_alpha_data_access.repositories import EventStudyRepository, RawDetailRepository

    labels = await EventStudyRepository(conn).list_for_training(
        asof_from=asof_from, asof_to=asof_to, universe_snapshot=universe
    )
    labeled = [r for r in labels if r[target] is not None]
    stock_ids = sorted({int(r["stock_id"]) for r in labeled})
    max_asof = max(r["asof_date"] for r in labeled)
    print(f"라벨 {len(labeled)}건({target}) · 종목 {len(stock_ids)} — 통합점수 재구성", flush=True)

    repo = RawDetailRepository(conn)
    caches = {}
    for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
        cfg = cfg_cls.from_env() if cfg_cls else None
        loader = (
            _PriceTrainingLoader(conn, window_days=3000) if kind == "price"
            else _build_loader(loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000), connection=conn)
        )
        rbs, sbs = {}, {}
        for sid in stock_ids:
            ev = await loader.load(stock_id=sid, stock_code="", as_of=max_asof)
            rbs[sid] = list(ev[0].metadata.get("rows") or []) if ev else []
            if loader_key == "hiring" and ev:
                sbs[sid] = ev[0].metadata.get("sector_demand")
        caches[src] = (kind, date_key, ind_fn, eval_fn, cfg, rbs, sbs)

    recs = []  # (agg_score, ret, asof, n_sources)
    for r in labeled:
        sid, asof = int(r["stock_id"]), r["asof_date"]
        scores = []
        for _src, (kind, date_key, ind_fn, eval_fn, cfg, rbs, sbs) in caches.items():
            pit = pit_rows(rbs.get(sid) or [], asof, date_key=date_key)
            if not pit:
                continue
            scores.append(await _score(kind, pit, asof, cfg, sbs.get(sid), ind_fn, eval_fn))
        if scores:
            recs.append((sum(scores) / len(scores), float(r[target]), asof, len(scores)))
    return recs


def sweep(recs: list) -> tuple[list, float | None]:
    scores = [x[0] for x in recs]
    rets = [x[1] for x in recs]
    dates = [x[2] for x in recs]
    ret_dm = _demean(rets, dates)  # abn = 시장중립
    # 연속 통합점수 IC(참고)
    comp_ic = _pearson(scores, ret_dm)
    rows = []
    for th in THETAS:
        pos = [ret_dm[i] for i in range(len(recs)) if scores[i] >= th]
        neg = [ret_dm[i] for i in range(len(recs)) if scores[i] <= -th]
        if len(pos) < MIN_N or len(neg) < MIN_N:
            rows.append((th, len(pos), len(neg), None, None, "gated"))
            continue
        mp, mn = sum(pos) / len(pos), sum(neg) / len(neg)
        spread = mp - mn
        vp = statistics.pvariance(pos) / len(pos) if len(pos) > 1 else 0.0
        vn = statistics.pvariance(neg) / len(neg) if len(neg) > 1 else 0.0
        se = (vp + vn) ** 0.5
        t = spread / se if se > 0 else 0.0
        rows.append((th, len(pos), len(neg), spread, t, "ok"))
    return rows, comp_ic


def _fmt(v):
    return "  n/a" if v is None else f"{v:+.4f}"


async def main(horizon: int, asof_from: date, asof_to: date, universe: str) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    target = f"fwd_return_{horizon}d"
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            recs = await build_aggregate(conn, asof_from=asof_from, asof_to=asof_to, universe=universe, target=target)
    finally:
        await pool.close()

    rows, comp_ic = sweep(recs)
    print("=" * 78)
    print(f"방향 컷오프 sweep — N={len(recs)} · horizon={horizon}d · abn(시장중립) · 등가중 통합점수")
    print(f"연속 통합점수 IC(abn) = {_fmt(comp_ic)}")
    print("=" * 78)
    print(f"{'θ':>5} {'n+':>5} {'n-':>5} {'spread':>9} {'t':>6}  게이트")
    ok = [r for r in rows if r[5] == "ok" and r[3] is not None]
    max_spread = max((r[3] for r in ok), default=0.0)
    band = [r[0] for r in ok if r[3] >= 0.9 * max_spread] if max_spread > 0 else []
    best = max(ok, key=lambda r: r[4], default=None)  # argmax t
    for th, npos, nneg, spread, t, gate in rows:
        star = " ←최대t" if (best and th == best[0]) else (" [밴드]" if th in band else "")
        print(f"{th:>5.2f} {npos:>5} {nneg:>5} {_fmt(spread):>9} {(f'{t:+.2f}' if t is not None else '  n/a'):>6}  {gate}{star}")
    print("-" * 78)
    if best:
        print(f"argmax-t θ = {best[0]:.2f} (spread {best[3]:+.4f}, t {best[4]:+.2f})")
    if band:
        print(f"유연 밴드(스프레드 ≥ 90% 최대·게이트통과): θ ∈ [{min(band):.2f}, {max(band):.2f}]")
    else:
        print("유연 밴드 없음(게이트 통과 θ 부족).")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, choices=(1, 5, 20))
    ap.add_argument("--asof-from", type=_d, default=_d("2021-01-01"))
    ap.add_argument("--asof-to", type=_d, default=_d("2026-06-01"))
    ap.add_argument("--universe", default="kospi20_seed")
    args = ap.parse_args()
    asyncio.run(main(args.horizon, args.asof_from, args.asof_to, args.universe))
