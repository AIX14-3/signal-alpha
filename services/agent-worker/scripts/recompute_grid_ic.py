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
import recompute_source_ic as _rc  # noqa: E402  (WF_BLOCKS 전역 조절용)
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


async def build_grid(conn, *, grid_days, horizons, asof_from) -> dict:
    series = await load_price_series(conn)  # {sid: (dates, closes)}
    stock_ids = sorted(series.keys())
    max_asof = max(series[sid][0][-1] for sid in stock_ids if series[sid][0])
    hmax = max(horizons)
    # 종목별 규칙 격자 — 모든 horizon 공유(같은 asof 집합 → 사과 대 사과 비교). 최대 horizon 만큼 미래 필요.
    grid = {sid: _grid_asofs(series[sid][0], grid_days, asof_from, hmax) for sid in stock_ids}
    n_asofs = sum(len(v) for v in grid.values())
    print(f"종목 {len(stock_ids)} · 격자 asof {n_asofs}건(간격 {grid_days}세션, {asof_from}~) · horizons {horizons}d", flush=True)

    from signal_alpha_data_access.repositories import RawDetailRepository
    repo = RawDetailRepository(conn)
    # triples_by[src][h] = [(score_100, fwd, asof), ...]
    triples_by = {s[0]: {h: [] for h in horizons} for s in SOURCES}
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

        for sid in stock_ids:
            for asof in grid[sid]:
                pit = pit_rows(rbs.get(sid) or [], asof, date_key=date_key)
                if not pit:
                    continue
                score = await _score(kind, pit, asof, cfg, sbs.get(sid), ind_fn, eval_fn)  # 점수는 1회만
                s100 = score * 50.0 + 50.0
                for h in horizons:
                    fwd = forward_return(series, sid, asof, h)
                    if fwd is not None:
                        triples_by[src][h].append((s100, float(fwd), asof))
        print(f"  {src} 점수 재계산 완료", flush=True)

    # horizon × source 지표
    by_h = {h: {src: _source_metrics(triples_by[src][h]) for src in triples_by} for h in horizons}
    return {"asofs": n_asofs, "by_h": by_h, "horizons": horizons}


async def main(horizons, grid_days, asof_from) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            results = await build_grid(conn, grid_days=grid_days, horizons=horizons, asof_from=asof_from)
    finally:
        await pool.close()
    by_h, hs = results["by_h"], results["horizons"]
    srcs = [s[0] for s in SOURCES]

    print("=" * 84)
    print(f"소스 × horizon 시장중립 IC (규칙격자, {asof_from}~, 간격 {grid_days}세션)")
    print("=" * 84)
    header = "  소스     " + "".join(f"{str(h) + 'd':>12}" for h in hs)
    print(header)
    for src in srcs:
        cells = []
        for h in hs:
            m = by_h[h][src]
            cells.append(f"{_fmt(m['ic_neutral'])}({m['n']})")
        note = " ⚠️자기참조" if src == "PRICE" else ""
        print(f"  {src:8} " + "".join(f"{c:>12}" for c in cells) + note)
    print("-" * 84)
    # horizon 별 권장 + 자격 후보(중립IC≥0.05·순열통과·워크포워드 전블록 양수)
    for h in hs:
        sug = _recommend(by_h[h], include_price=False)
        print(f"  [{h:>2}d] 권장 {sug['mode']:6} — {sug['rationale']}")
    print("-" * 84)
    print("해석: 셀=시장중립 IC(N). 짧은 horizon에서 어떤 소스가 IC≥0.05·유의·시간일관을 만족하는지 확인.")
    print("      (워크포워드 상세는 필요 시 --horizons 단일값으로 재실행 — _source_metrics 에 보존됨.)")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="1,3,5,10,20", help="forward return 기간(콤마 리스트, 거래일)")
    ap.add_argument("--grid-days", type=int, default=20, help="asof 간격(거래일). 20≈월별")
    ap.add_argument("--asof-from", type=_d, default=_d("2019-01-01"))
    ap.add_argument("--wf-blocks", type=int, default=_rc.WF_BLOCKS, help="워크포워드 시간분할 블록 수")
    args = ap.parse_args()
    _rc.WF_BLOCKS = args.wf_blocks  # 규칙격자 워크포워드 블록 수 반영
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    asyncio.run(main(horizons, args.grid_days, args.asof_from))
