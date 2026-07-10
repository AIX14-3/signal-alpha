"""HIRING 단기 신호 — 종목별 기여 분해 (소수 종목 집중 vs 고른 분포).

(A) 2단계. h=3/5d date-demean IC 를 종목별로 쪼갠다:
- 종목별 공분산 기여(Σ ds·dr)·부호·표본수 → 몇 종목이 IC 를 끄나.
- Leave-one-out IC(각 종목 빼고 재계산) → 한 종목 빼면 무너지나(집중) vs 안정(분산).
- 양(+)기여 종목 수, 상위 3종목 집중도.
집중되면 취약(소수 종목 운), 고르면 견고. LOCAL DB only.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))
sys.path.insert(0, str(_HERE.parent))

import asyncpg  # noqa: E402

from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _build_loader  # noqa: E402
from ic_diagnostic import forward_return, load_price_series  # noqa: E402
from recompute_grid_ic import _grid_asofs  # noqa: E402
from recompute_source_ic import SOURCES, _pearson, _score  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HORIZONS = [3, 5]
GRID_DAYS = 20
ASOF_FROM = date(2019, 1, 1)


def _demean(vals, keys):
    g = defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = {k: sum(v) / len(v) for k, v in g.items()}
    return [v - m[k] for v, k in zip(vals, keys)]


async def _hiring_base(conn):
    series = await load_price_series(conn)
    sids = sorted(series.keys())
    maxa = max(series[s][0][-1] for s in sids if series[s][0])
    src, kind, lk, dk, ind, ev, cfgc = next(s for s in SOURCES if s[0] == "HIRING")
    cfg = cfgc.from_env()
    from signal_alpha_data_access.repositories import RawDetailRepository
    loader = _build_loader(lk, RawDetailRepository(conn), max(getattr(cfg, "lookback_days", 0), 4500), connection=conn)
    tickers = {int(r["id"]): r["ticker"] for r in await conn.fetch("SELECT id, ticker FROM stocks")}
    base = []
    for sid in sids:
        e = await loader.load(stock_id=sid, stock_code="", as_of=maxa)
        rows = list(e[0].metadata.get("rows") or []) if e else []
        sector = e[0].metadata.get("sector_demand") if e else None
        for asof in _grid_asofs(series[sid][0], GRID_DAYS, ASOF_FROM, max(HORIZONS)):
            pit = pit_rows(rows, asof, date_key=dk)
            if not pit:
                continue
            sc = await _score(kind, pit, asof, cfg, sector, ind, ev)
            base.append((sc * 50 + 50, sid, asof))
    return series, base, tickers


async def main() -> None:
    db = os.environ.get("DATABASE_URL", "")
    if "localhost" not in db and "127.0.0.1" not in db:
        raise SystemExit("LOCAL only")
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            series, base, tickers = await _hiring_base(conn)
    finally:
        await pool.close()

    for h in HORIZONS:
        rows = []
        for s100, sid, asof in base:
            f = forward_return(series, sid, asof, h)
            if f is not None:
                rows.append((s100, float(f), sid, asof))
        s = [r[0] for r in rows]
        r_ = [r[1] for r in rows]
        sid_ = [r[2] for r in rows]
        dd = [r[3] for r in rows]
        ds = _demean(s, dd)  # date-demean(시장중립)
        dr = _demean(r_, dd)
        ic = _pearson(ds, dr)
        n_stocks = len(set(sid_))
        # 종목별 공분산 기여
        contrib = defaultdict(float)
        cnt = defaultdict(int)
        for i in range(len(rows)):
            contrib[sid_[i]] += ds[i] * dr[i]
            cnt[sid_[i]] += 1
        total = sum(contrib.values())
        pos = [x for x in contrib.values() if x > 0]
        ranked = sorted(contrib.items(), key=lambda kv: kv[1], reverse=True)
        top3 = sum(v for _, v in ranked[:3])
        # Leave-one-out IC (종목 빼고 date-demean 재계산)
        loo = []
        for drop in set(sid_):
            idx = [i for i in range(len(rows)) if sid_[i] != drop]
            xs = _demean([s[i] for i in idx], [dd[i] for i in idx])
            ys = _demean([r_[i] for i in idx], [dd[i] for i in idx])
            loo.append((_pearson(xs, ys), drop))
        loo_vals = [v for v, _ in loo if v is not None]
        worst = min(loo, key=lambda kv: (kv[0] if kv[0] is not None else 9))

        print(f"\n===== HIRING h={h}d · N={len(rows)} · 종목 {n_stocks} · 전체 date-demean IC {ic:+.3f} =====")
        print(f"  양(+)기여 종목: {len(pos)}/{n_stocks}  · 상위3종목 공분산 집중도: {top3/total:.0%}" if total else "  (total 0)")
        print(f"  Leave-one-out IC: min {min(loo_vals):+.3f} ~ max {max(loo_vals):+.3f} "
              f"(중앙 {sorted(loo_vals)[len(loo_vals)//2]:+.3f}); 가장 크게 낮추는 종목 뺐을때 {worst[0]:+.3f}({tickers.get(worst[1])})")
        print("  상위 기여 종목(공분산):")
        for sid, v in ranked[:5]:
            print(f"    {tickers.get(sid,'?'):>8}  기여 {v:+.4f}  표본 {cnt[sid]}  (전체의 {v/total:+.0%})" if total else "")
        print("  하위(음의 기여) 종목:")
        for sid, v in ranked[-3:]:
            print(f"    {tickers.get(sid,'?'):>8}  기여 {v:+.4f}  표본 {cnt[sid]}")


if __name__ == "__main__":
    asyncio.run(main())
