"""IC 진단: 6소스 통합 점수와 소스별 점수가 선행수익률(주가 방향)을 얼마나 맞히나?

각 발행 신호(stock_id, signal_date)를 +k거래일 선행수익률(ohlcv_data)에 조인해 IC(Pearson)·
hit-rate(부호 일치)·tercile-lift·순열검정을 통합 점수와 **소스별**(score_breakdown)로 계산한다.
그리고 소스별 20일 IC로부터 **권장 가중치(ALT_WEIGHT_*)** 와 **권장 모드**를 산출한다 — 이것이
`AggregatorConfig.weight_mode="ic"` 를 채우는 근거다.

핵심 설계: 가중은 IC 진단이 근거를 보일 때만 켠다. 양의 IC 소스가 없으면 권장은 등가중(equal).

⚠️ PRICE 자기참조: PRICE 점수는 과거 가격에서 파생 → 미래 가격 대비 IC 는 기술적분석 자기참조라
   대체데이터 알파가 아니다. 표기만 하고 권장 가중에서는 기본 제외(--include-price 로 포함).
⚠️ 소표본/혼합 스코어링 버전: 로컬 패널은 소수 종목·짧은 창이라 수치는 참고용. 1차 목표는
   재실행 가능한 하니스 + 근거 산출이지 유의성 주장 아님.

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    python scripts/ic_diagnostic.py                 # 진단 + 권장 가중치
    python scripts/ic_diagnostic.py --json out.json  # 기계판독 요약
    python scripts/ic_diagnostic.py --smoke          # 하니스 자체검증(합성 IC>0 검출)

(backtest_integrated_score.py 하니스를 정리·편입하고 권장 가중치 산출을 추가한 버전.)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from bisect import bisect_left
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "data-access"))

import asyncpg  # noqa: E402

try:  # Windows 콘솔(cp949)에서 ★·—·이모지·화살표 출력이 깨지는 것 방지
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — 재설정 불가한 스트림이면 그냥 진행
    pass

HORIZONS = (1, 5, 20)
WEIGHT_HORIZON = 20            # 권장 가중치는 20일 IC 기준(공통 타깃)
SCORING_SOURCES = ("PRICE", "DART", "REPORT", "PATENT", "HIRING", "DATALAB")
MIN_N = 20                     # soft gate: 이 미만은 저신뢰로 표기하고 가중에서 제외
PERM_ITERS = 2000
PERM_P_GATE = 0.10             # 권장 가중에 넣으려면 순열검정 p ≤ 이 값


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #
async def load_price_series(conn) -> dict[int, tuple[list[date], list[float]]]:
    rows = await conn.fetch(
        "SELECT stock_id, trade_date, close FROM ohlcv_data ORDER BY stock_id, trade_date"
    )
    series: dict[int, tuple[list[date], list[float]]] = {}
    for r in rows:
        d, c = series.setdefault(int(r["stock_id"]), ([], []))
        d.append(r["trade_date"])
        c.append(float(r["close"]))
    return series


async def load_signal_panel(conn) -> list[dict]:
    """발행된 통합 신호 + 소스별 breakdown(PIT)."""
    rows = await conn.fetch(
        """
        SELECT stock_id, signal_date, final_score, signal, score_breakdown
        FROM final_signals
        WHERE is_current
        ORDER BY signal_date, stock_id
        """
    )
    panel = []
    for r in rows:
        bd = r["score_breakdown"]
        bd = json.loads(bd) if isinstance(bd, str) else (bd or {})
        panel.append({
            "stock_id": int(r["stock_id"]),
            "date": r["signal_date"],
            "final_score": float(r["final_score"]),
            "signal": r["signal"],
            "breakdown": bd,
        })
    return panel


def forward_return(series, stock_id: int, t0: date, k: int) -> float | None:
    """진입 = signal_date 이상 첫 세션, +k세션 미경과면 None(미실현 미래 배제, look-ahead 0)."""
    entry = series.get(stock_id)
    if not entry:
        return None
    dates, closes = entry
    i0 = bisect_left(dates, t0)
    if i0 >= len(dates) or i0 + k >= len(dates):
        return None
    base = closes[i0]
    return (closes[i0 + k] / base - 1.0) if base else None


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def _hit_rate(pairs: list[tuple[float, float]]) -> float | None:
    """sign(score-50) 가 sign(return) 과 일치(중립 score==50 제외)."""
    hits = tot = 0
    for s, r in pairs:
        d = s - 50.0
        if d == 0 or r == 0:
            continue
        tot += 1
        if (d > 0) == (r > 0):
            hits += 1
    return hits / tot if tot else None


def _tercile_lift(pairs: list[tuple[float, float]]):
    if len(pairs) < 6:
        return None
    ordered = sorted(pairs, key=lambda p: p[0])
    k = len(ordered) // 3
    low = [r for _, r in ordered[:k]]
    high = [r for _, r in ordered[-k:]]
    return _mean(low), _mean(high), _mean(high) - _mean(low)


def _perm_pvalue(pairs: list[tuple[float, float]], iters: int) -> float | None:
    """IC 크기 순열검정: 수익률을 결정론적으로 회전시켜 |IC| 재계산."""
    n = len(pairs)
    if n < MIN_N:
        return None
    xs = [s for s, _ in pairs]
    ys = [r for _, r in pairs]
    obs = _pearson(xs, ys)
    if obs is None:
        return None
    extreme = 0
    for i in range(1, iters + 1):
        stride = 1 + (i % (n - 1))
        rot = ys[stride:] + ys[:stride]
        r = _pearson(xs, rot)
        if r is not None and abs(r) >= abs(obs):
            extreme += 1
    return extreme / iters


# --------------------------------------------------------------------------- #
# pair building + metrics
# --------------------------------------------------------------------------- #
def _abnormal_pairs(panel, series, k: int) -> list[tuple[float, float]]:
    """(final_score, 초과수익) — 초과 = 종목 k일 수익 − 같은 날 코호트 평균(시장상대)."""
    by_date: dict[date, list[float]] = {}
    raw = []
    for row in panel:
        fr = forward_return(series, row["stock_id"], row["date"], k)
        if fr is None:
            continue
        by_date.setdefault(row["date"], []).append(fr)
        raw.append((row["final_score"], row["date"], fr))
    bench = {d: _mean(v) for d, v in by_date.items()}
    return [(s, fr - bench[d]) for s, d, fr in raw]


def _composite_pairs(panel, series, k: int) -> list[tuple[float, float]]:
    out = []
    for row in panel:
        fr = forward_return(series, row["stock_id"], row["date"], k)
        if fr is not None:
            out.append((row["final_score"], fr))
    return out


def _source_pairs(panel, series, src: str, k: int) -> list[tuple[float, float]]:
    out = []
    for row in panel:
        e = row["breakdown"].get(src)
        # 신형 breakdown 만 소스별 100점 노출(구형 float → 스킵).
        if not isinstance(e, dict) or not e.get("contributes_to_score"):
            continue
        s100 = e.get("score_100")
        if s100 is None:
            continue
        fr = forward_return(series, row["stock_id"], row["date"], k)
        if fr is not None:
            out.append((float(s100), fr))
    return out


def _metrics(pairs: list[tuple[float, float]]) -> dict:
    xs = [s for s, _ in pairs]
    ys = [r for _, r in pairs]
    lift = _tercile_lift(pairs)
    return {
        "n": len(pairs),
        "ic": _pearson(xs, ys),
        "hit_rate": _hit_rate(pairs),
        "tercile_lift": (lift[2] if lift else None),
        "perm_p": _perm_pvalue(pairs, PERM_ITERS),
    }


# --------------------------------------------------------------------------- #
# 권장 가중치 산출
# --------------------------------------------------------------------------- #
def suggest_weights(src_res: dict[str, dict], *, include_price: bool) -> dict:
    """소스별 20일 IC → 권장 ALT_WEIGHT_* + 권장 weight_mode.

    자격: N ≥ MIN_N, IC > 0, 순열검정 p ≤ PERM_P_GATE. 자격 소스가 없으면 등가중 권장.
    자격 소스는 IC 비례 가중(자격 소스 평균≈1 로 정규화), 비자격 소스는 0(명시). PRICE 는
    자기참조라 기본 제외.
    """
    eligible = {}
    for src, m in src_res.items():
        if src == "PRICE" and not include_price:
            continue
        ic, n, p = m.get("ic"), m.get("n", 0), m.get("perm_p")
        if ic is not None and ic > 0 and n >= MIN_N and (p is not None and p <= PERM_P_GATE):
            eligible[src] = ic
    if not eligible:
        return {
            "mode": "equal",
            "weights": {},
            "rationale": "유의미한 양의 IC 소스 없음(N·부호·순열검정 미충족) → 등가중 유지 권장.",
        }
    total = sum(eligible.values())
    scale = len(eligible)  # 자격 소스 평균 가중 ≈ 1 로 스케일(친숙한 범위 유지)
    weights = {src: 0.0 for src in SCORING_SOURCES}
    for src, ic in eligible.items():
        weights[src] = round(ic / total * scale, 3)
    return {
        "mode": "ic",
        "weights": weights,
        "rationale": f"{sorted(eligible)} 가 유의미 양의 IC → IC 비례 가중. 나머지 0.",
    }


# --------------------------------------------------------------------------- #
# smoke
# --------------------------------------------------------------------------- #
def build_smoke_panel(series) -> list[dict]:
    """실가격 위에 '미래를 부분적으로 아는' 합성 점수 패널 — 하니스가 IC>0 을 실제 검출하는지
    검증용(알파 주장 아님). 결정론(RNG 없음). score=50+500*fwd20+노이즈 → 20일 IC 크게 양수 기대."""
    panel = []
    for sid, (dates, _closes) in series.items():
        for i in range(0, len(dates), 5):
            d = dates[i]
            fwd = forward_return(series, sid, d, 20)
            if fwd is None:
                continue
            noise = 6.0 * math.sin(sid * 3.7 + i * 0.9)
            score = max(0.0, min(100.0, 50.0 + 500.0 * fwd + noise))
            panel.append({
                "stock_id": sid, "date": d, "final_score": score,
                "signal": "positive" if score >= 55 else "neutral",
                # smoke 는 통합 점수를 소스별에도 복제해 소스 IC 경로까지 검증.
                "breakdown": {
                    src: {"score_100": score, "contributes_to_score": True}
                    for src in SCORING_SOURCES
                },
            })
    return panel


def _fmt(v, pct=False):
    if v is None:
        return "   n/a"
    return f"{v * 100:+6.2f}%" if pct else f"{v:+6.3f}"


async def main(json_out: str | None, smoke: bool, include_price: bool) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            series = await load_price_series(conn)
            panel = build_smoke_panel(series) if smoke else await load_signal_panel(conn)
    finally:
        await pool.close()
    if smoke:
        print("★ SMOKE 모드 — 실가격 위 합성 상관 패널(하니스 검증용, 알파 주장 아님)")

    distinct = sorted({round(r["final_score"], 2) for r in panel})
    newfmt = sum(1 for r in panel if any(isinstance(v, dict) for v in r["breakdown"].values()))
    results = {"panel_rows": len(panel), "distinct_final_scores": len(distinct),
               "newformat_breakdown_rows": newfmt, "horizons": {}}
    print("=" * 76)
    print(f"IC 진단 — 신호 {len(panel)}건 (선행수익률 계산 가능분만 각 지평선 집계)")
    print(f"  final_score 고유값 {len(distinct)}개 | 신형 breakdown {newfmt}건")
    if len(distinct) < 2:
        print("  ⚠️ final_score 변별력 없음(사실상 상수) → IC 측정 불가. 신선 패널 재집계/확충 필요.")
    print("=" * 76)

    for k in HORIZONS:
        m = _metrics(_composite_pairs(panel, series, k))
        ma = _metrics(_abnormal_pairs(panel, series, k))
        results["horizons"][f"{k}d"] = {"composite": m, "abnormal": ma}
        gate = "" if m["n"] >= MIN_N else "  ⚠️저N"
        print(f"\n[{k}일 지평선]  N={m['n']}{gate}")
        print(f"  통합 final_score : IC {_fmt(m['ic'])} | hit {_fmt(m['hit_rate'], True)} | "
              f"tercile-lift {_fmt(m['tercile_lift'], True)} | perm-p {m['perm_p']}")
        print(f"  초과수익(vs코호트): IC {_fmt(ma['ic'])} | hit {_fmt(ma['hit_rate'], True)}")

    print(f"\n[소스별 IC · {WEIGHT_HORIZON}일 지평선]")
    src_res = {}
    for src in SCORING_SOURCES:
        mm = _metrics(_source_pairs(panel, series, src, WEIGHT_HORIZON))
        src_res[src] = mm
        note = " (⚠️자기참조)" if src == "PRICE" else ""
        print(f"  {src:8} N={mm['n']:4}  IC {_fmt(mm['ic'])}  hit {_fmt(mm['hit_rate'], True)}  "
              f"perm-p {mm['perm_p']}{note}")
    results["source_ic_20d"] = src_res

    suggestion = suggest_weights(src_res, include_price=include_price)
    results["suggested"] = suggestion
    print("\n" + "-" * 76)
    print(f"권장: weight_mode = {suggestion['mode']}  ({suggestion['rationale']})")
    if suggestion["mode"] == "ic":
        print("  → 아래를 env 로 설정하면 IC 가중이 켜진다(검토 후 적용):")
        print("     ALT_WEIGHT_MODE=ic")
        for src, w in suggestion["weights"].items():
            print(f"     ALT_WEIGHT_{src}={w}")
    print("해석: IC 부호=점수와 선행수익률 상관(양수=예측적). 소표본·혼합버전이면 참고용.")

    if json_out:
        Path(json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON 요약 → {json_out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="기계판독 요약 저장 경로")
    ap.add_argument("--smoke", action="store_true", help="합성 상관 패널로 하니스 자체검증")
    ap.add_argument("--include-price", action="store_true",
                    help="권장 가중에 PRICE 포함(기본 제외 — 자기참조)")
    args = ap.parse_args()
    asyncio.run(main(args.json, args.smoke, args.include_price))
