"""규칙적 날짜 격자 recompute-IC — 이벤트 앵커(signal_events) 대신 월별 asof 격자.

event_study_panel 은 이벤트 앵커라 과거엔 희박하다. 하지만 원본(특허 12만·OHLCV 2015~)은
매 시점 점수를 낼 만큼 조밀하다. 여기선 종목마다 grid_days 세션 간격의 규칙 격자를 만들어
소스 점수를 재계산(pit 누수차단)하고, 선행수익률은 OHLCV 에서 직접 계산한다. → PATENT/PRICE 를
2019~2026(2020 코로나 급락 레짐 포함) 조밀하게 검증.

Run: python scripts/recompute_grid_ic.py --horizon 20 --grid-days 20 --asof-from 2019-01-01
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import os
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
from ic_diagnostic import _fmt, forward_return, load_price_series  # noqa: E402
from recompute_source_ic import SOURCES, _recommend, _score, _source_metrics  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _require_local(db: str) -> None:
    if "localhost" not in db and "127.0.0.1" not in db:
        raise SystemExit("LOCAL only")


def _grid_asofs(dates: list, grid_days: int, asof_from: date, horizon: int) -> list:
    """정렬된 거래일 리스트에서 asof_from 이후 grid_days 간격, 선행수익 계산 가능분만."""
    start = bisect.bisect_left(dates, asof_from)
    out = []
    i = start
    while i < len(dates) - horizon:
        out.append(dates[i])
        i += grid_days
    return out


async def build_grid(conn, *, grid_days, horizon, asof_from) -> dict:
    series = await load_price_series(conn)  # {sid: (dates, closes)}
    stock_ids = sorted(series.keys())
    max_asof = max(series[sid][0][-1] for sid in stock_ids if series[sid][0])
    # 종목별 규칙 격자
    grid = {sid: _grid_asofs(series[sid][0], grid_days, asof_from, horizon) for sid in stock_ids}
    n_asofs = sum(len(v) for v in grid.values())
    print(f"종목 {len(stock_ids)} · 격자 asof {n_asofs}건(간격 {grid_days}세션, {asof_from}~) · horizon {horizon}d", flush=True)

    from signal_alpha_data_access.repositories import RawDetailRepository
    repo = RawDetailRepository(conn)
    src_res = {}
    for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
        cfg = cfg_cls.from_env() if cfg_cls else None
        loader = (
            _PriceTrainingLoader(conn, window_days=4500) if kind == "price"
            else _build_loader(loader_key, repo, max(getattr(cfg, "lookback_days", 0), 4500), connection=conn)
        )
        rbs, sbs = {}, {}
        for sid in stock_ids:
            ev = await loader.load(stock_id=sid, stock_code="", as_of=max_asof)
            rbs[sid] = list(ev[0].metadata.get("rows") or []) if ev else []
            if loader_key == "hiring" and ev:
                sbs[sid] = ev[0].metadata.get("sector_demand")

        triples = []
        for sid in stock_ids:
            for asof in grid[sid]:
                pit = pit_rows(rbs.get(sid) or [], asof, date_key=date_key)
                if not pit:
                    continue
                fwd = forward_return(series, sid, asof, horizon)
                if fwd is None:
                    continue
                score = await _score(kind, pit, asof, cfg, sbs.get(sid), ind_fn, eval_fn)
                triples.append((score * 50.0 + 50.0, float(fwd), asof))
        src_res[src] = _source_metrics(triples)
        m = src_res[src]
        wf = "[" + ",".join(_fmt(x).strip() for x in (m.get("wf_ics") or [])) + "]"
        note = " (⚠️자기참조)" if src == "PRICE" else ""
        print(f"  ▸ {src:8} N={m['n']:6}  중립-IC {_fmt(m['ic_neutral'])}  강신호-IC {_fmt(m['ic_neutral_strong'])}  "
              f"워크포워드 {wf}  perm-p {m['perm_p_neutral']}{note}", flush=True)
    return {"asofs": n_asofs, "source_ic": src_res}


async def main(horizon, grid_days, asof_from) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            results = await build_grid(conn, grid_days=grid_days, horizon=horizon, asof_from=asof_from)
    finally:
        await pool.close()
    print("=" * 80)
    sug = _recommend(results["source_ic"], include_price=False)
    print(f"권장: weight_mode = {sug['mode']}  ({sug['rationale']})")
    print("해석: 규칙 격자라 이벤트 앵커 희박 문제 없음. 특허/주가는 2019~ 조밀(2020 급락 포함).")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=20, choices=(1, 5, 20))
    ap.add_argument("--grid-days", type=int, default=20, help="asof 간격(거래일). 20≈월별")
    ap.add_argument("--asof-from", type=_d, default=_d("2019-01-01"))
    args = ap.parse_args()
    asyncio.run(main(args.horizon, args.grid_days, args.asof_from))
