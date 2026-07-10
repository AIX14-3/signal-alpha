"""HIRING 단기 후보신호 압박 — 섹터/종목 교란 제거 후 IC 가 살아남나.

date-demean(현 시장중립)은 시장 전체만 제거한다. 이 스크립트는 더 엄격한 3중 demean 으로
HIRING h=3/5/10d IC 를 재본다:
  1) date        = 같은 asof 평균 제거(시장). 현 시장중립 IC = 기준선.
  2) firm(within) = 같은 종목 평균 제거. 섹터·크기·모든 정적 특성 통째 제거 → 순수 종목내 타이밍.
  3) sector×date  = 같은 (섹터,asof) 평균 제거(시장+섹터시점 교란). ⚠️섹터 파편화로 유효 N 급감 가능.
firm 에서도 IC 가 양수·유의·워크포워드 일관이면 진짜 타이밍 신호. 무너지면 정적/섹터 특성.

LOCAL DB only. Run: python scripts/verify_hiring_sector_neutral.py
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
from recompute_source_ic import SOURCES, _pearson, _perm_pvalue, _score  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HORIZONS = [3, 5, 10]
GRID_DAYS = 20
ASOF_FROM = date(2019, 1, 1)


def _demean(vals, keys):
    g = defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = {k: sum(v) / len(v) for k, v in g.items()}
    return [v - m[k] for v, k in zip(vals, keys)]


def _demean_drop_singletons(vals, keys):
    """그룹원이 <2 면 None(그 표본은 제외 대상). 그룹 평균 제거."""
    g = defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = {k: sum(v) / len(v) for k, v in g.items()}
    cnt = {k: len(v) for k, v in g.items()}
    return [(v - m[k] if cnt[k] >= 2 else None) for v, k in zip(vals, keys)]


def _wf(rs, rr, dates, k=4):
    uniq = sorted(set(dates))
    if len(uniq) < k:
        return []
    size = len(uniq) / k
    out = []
    for i in range(k):
        blk = set(uniq[int(i * size):int((i + 1) * size)])
        idx = [j for j, d in enumerate(dates) if d in blk]
        out.append(_pearson([rs[j] for j in idx], [rr[j] for j in idx]))
    return out


def _fmt(v):
    return "  n/a" if v is None else f"{v:+.3f}"


async def main() -> None:
    db = os.environ.get("DATABASE_URL", "")
    if "localhost" not in db and "127.0.0.1" not in db:
        raise SystemExit("LOCAL only")
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            series = await load_price_series(conn)
            sids = sorted(series.keys())
            maxa = max(series[s][0][-1] for s in sids if series[s][0])
            sector = {int(r["id"]): r["sector"] for r in await conn.fetch("SELECT id, sector FROM stocks")}
            src, kind, lk, dk, ind, ev, cfgc = next(s for s in SOURCES if s[0] == "HIRING")
            cfg = cfgc.from_env()
            from signal_alpha_data_access.repositories import RawDetailRepository
            loader = _build_loader(lk, RawDetailRepository(conn), max(getattr(cfg, "lookback_days", 0), 4500), connection=conn)
            rbs, sbs = {}, {}
            for sid in sids:
                e = await loader.load(stock_id=sid, stock_code="", as_of=maxa)
                rbs[sid] = list(e[0].metadata.get("rows") or []) if e else []
                if e:
                    sbs[sid] = e[0].metadata.get("sector_demand")
            # 점수 1회 재계산
            base = []  # (s100, sid, asof)
            for sid in sids:
                for asof in _grid_asofs(series[sid][0], GRID_DAYS, ASOF_FROM, max(HORIZONS)):
                    pit = pit_rows(rbs.get(sid) or [], asof, date_key=dk)
                    if not pit:
                        continue
                    sc = await _score(kind, pit, asof, cfg, sbs.get(sid), ind, ev)
                    base.append((sc * 50 + 50, sid, asof))
    finally:
        await pool.close()

    print(f"HIRING 섹터/종목-중립 검증 — 점수표본 {len(base)}건 (격자 {GRID_DAYS}세션, {ASOF_FROM}~)")
    print("=" * 88)
    print(f"{'h':>3} {'demean':>12} {'N':>6} {'IC':>8} {'perm-p':>8}  워크포워드4블록")
    for h in HORIZONS:
        tr = []
        for s100, sid, asof in base:
            f = forward_return(series, sid, asof, h)
            if f is not None:
                tr.append((s100, float(f), sid, asof))
        if not tr:
            continue
        s = [t[0] for t in tr]
        r = [t[1] for t in tr]
        sd = [t[2] for t in tr]
        dd = [t[3] for t in tr]
        # 1) date  2) firm  3) sector×date
        variants = {
            "date": (_demean(s, dd), _demean(r, dd), dd),
            "firm(within)": (_demean(s, sd), _demean(r, sd), dd),
        }
        # sector×date: drop singleton (sector,asof)
        seckey = [(sector.get(t[2]), t[3]) for t in tr]
        rs = _demean_drop_singletons(s, seckey)
        rr = _demean_drop_singletons(r, seckey)
        keep = [i for i in range(len(tr)) if rs[i] is not None and rr[i] is not None]
        variants["sector×date"] = ([rs[i] for i in keep], [rr[i] for i in keep], [dd[i] for i in keep])
        for name, (xs, ys, ds) in variants.items():
            ic = _pearson(xs, ys)
            p = _perm_pvalue(list(zip(xs, ys)), 2000) if len(xs) >= 20 else None
            wf = _wf(xs, ys, ds, 4)
            wfs = "[" + ",".join(_fmt(x).strip() for x in wf) + "]"
            print(f"{h:>3} {name:>12} {len(xs):>6} {_fmt(ic):>8} {str(p):>8}  {wfs}")
        print("-" * 88)


if __name__ == "__main__":
    asyncio.run(main())
