"""(B) DART/patent 짧은 horizon 다른 각도 — HIRING처럼 숨은 단기신호 있나.

DART raw IC(+0.35)는 상승장 base-rate 아티팩트였다(내부자 95% 순매수). 진짜 단기신호를 찾으려면
(1) within-firm demean(종목 정적/base-rate 제거) (2) holder_type별 분해(executive=내부자 vs
major=대량보유 vs main_shareholder). patent는 전 horizon ≈0이나 within-firm/신규분류 각도로 재확인.

h=3d 중심. date-demean(시장중립)·firm(within) IC + 워크포워드. LOCAL DB only.
Run: python scripts/explore_dart_patent_short.py
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

from app.analyzers.dart.source_result import build_dart_analysis_result  # noqa: E402
from app.ml.source_features import pit_rows  # noqa: E402
from app.ml.train_source_models import _build_loader  # noqa: E402
from ic_diagnostic import forward_return, load_price_series  # noqa: E402
from recompute_grid_ic import _grid_asofs  # noqa: E402
from recompute_source_ic import (  # noqa: E402
    SOURCES,
    _dart_blite_events,
    _pearson,
    _perm_pvalue,
    _score,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

H = 3
GRID_DAYS = 20
ASOF_FROM = date(2019, 1, 1)


def _demean(vals, keys):
    g = defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = {k: sum(v) / len(v) for k, v in g.items()}
    return [v - m[k] for v, k in zip(vals, keys)]


def _wf(xs, ys, ds, k=4):
    uniq = sorted(set(ds))
    if len(uniq) < k:
        return []
    size = len(uniq) / k
    out = []
    for i in range(k):
        blk = set(uniq[int(i * size):int((i + 1) * size)])
        idx = [j for j, d in enumerate(ds) if d in blk]
        out.append(_pearson([xs[j] for j in idx], [ys[j] for j in idx]))
    return out


def _fmt(v):
    return "  n/a" if v is None else f"{v:+.3f}"


def _report(name, triples):
    """triples=(score_100, fwd, sid, asof). date/firm demean IC + perm-p + 워크포워드."""
    if len(triples) < 20:
        print(f"  {name:26} N={len(triples):>5}  (표본 부족)")
        return
    s = [t[0] for t in triples]
    r = [t[1] for t in triples]
    sd = [t[2] for t in triples]
    dd = [t[3] for t in triples]
    for tag, keys in [("date", dd), ("firm", sd)]:
        xs, ys = _demean(s, keys), _demean(r, keys)
        ic = _pearson(xs, ys)
        p = _perm_pvalue(list(zip(xs, ys)), 2000)
        wf = "[" + ",".join(_fmt(x).strip() for x in _wf(xs, ys, dd)) + "]"
        print(f"  {name:26} N={len(triples):>5} {tag:>4}  IC {_fmt(ic)}  perm-p {str(p):>7}  {wf}")


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
            from signal_alpha_data_access.repositories import RawDetailRepository
            repo = RawDetailRepository(conn)

            # ---- DART: holder_type별 B-lite 점수 ----
            dloader = _build_loader("dart", repo, 4500, connection=conn)
            drows = {}
            for sid in sids:
                e = await dloader.load(stock_id=sid, stock_code="", as_of=maxa)
                drows[sid] = list(e[0].metadata.get("rows") or []) if e else []
            print(f"===== DART h={H}d · holder_type별 (B-lite 방향점수) =====")
            for label, ht in [("all", None), ("executive(내부자)", {"executive"}),
                              ("major(대량보유)", {"major"}), ("main_shareholder", {"main_shareholder"})]:
                tri = []
                for sid in sids:
                    for asof in _grid_asofs(series[sid][0], GRID_DAYS, ASOF_FROM, H):
                        pit = pit_rows(drows.get(sid) or [], asof, date_key="report_date")
                        if ht is not None:
                            pit = [e for e in pit if e.get("holder_type") in ht]
                        if not pit:
                            continue
                        f = forward_return(series, sid, asof, H)
                        if f is None:
                            continue
                        sc = build_dart_analysis_result(_dart_blite_events(pit)).score
                        tri.append((sc * 50 + 50, float(f), sid, asof))
                _report(f"DART {label}", tri)

            # ---- PATENT: 전체 점수 + 신규분류만 ----
            print(f"\n===== PATENT h={H}d =====")
            _psrc = next(s for s in SOURCES if s[0] == "PATENT")
            _, kind, lk, dk, ind, ev, cfgc = _psrc
            cfg = cfgc.from_env()
            ploader = _build_loader(lk, repo, max(getattr(cfg, "lookback_days", 0), 4500), connection=conn)
            prows = {}
            for sid in sids:
                e = await ploader.load(stock_id=sid, stock_code="", as_of=maxa)
                prows[sid] = list(e[0].metadata.get("rows") or []) if e else []
            for label, only_new in [("전체 점수", False), ("신규분류 rows만", True)]:
                tri = []
                for sid in sids:
                    for asof in _grid_asofs(series[sid][0], GRID_DAYS, ASOF_FROM, H):
                        pit = pit_rows(prows.get(sid) or [], asof, date_key=dk)
                        if only_new:
                            pit = [r for r in pit if r.get("is_new_category")]
                        if not pit:
                            continue
                        f = forward_return(series, sid, asof, H)
                        if f is None:
                            continue
                        sc = await _score(kind, pit, asof, cfg, None, ind, ev)
                        tri.append((sc * 50 + 50, float(f), sid, asof))
                _report(f"PATENT {label}", tri)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
