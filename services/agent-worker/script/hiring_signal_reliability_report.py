"""Hiring 신호 신뢰도 측정 하니스 (#298) — READ-ONLY.

`final_signals.confidence` 는 시스템 자기보고(low_base/stale/coverage/agreement 페널티)라
단순 `AVG(confidence)` 는 자기채점 순환이다. 본 리포트는 3계층으로 신뢰도를 *독립* 측정한다:

  L1 자기보고  : confidence/consensus 분포, source_agreement, warning, needs_review, caution 플래그 빈도.
  L2 독립 신뢰 : ① 안정성(day-over-day signal flip율·score 변동) ② 표본 실태(저표본 비율)
                ③ 발견 아티팩트(score 급변일 ↔ 수집 커버리지 급변) ④ 단일소스 의존 ⑤ 신선도.
  L3 보정      : confidence(=source_agreement) 버킷별 L2 안정성 — HIGH 가 실제로 덜 뒤집히나.
  + 합성 신뢰도지수(0~100, L2 기반) + 빨강 플래그.

지표는 **순수 함수**로 분리해 DB 없이 단위테스트 가능(test_hiring_reliability_report.py).
DB 접근은 전부 SELECT(쓰기 0). 현재 hiring 데이터는 웜업(수일치)이라 대부분 "표본 부족"으로 보고된다.

USAGE
    uv run python script/hiring_signal_reliability_report.py --date 2026-06-24 --lookback 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent-worker root (run_analyzers)

from run_analyzers import load_env, parse_dsn, resolve_ssl  # noqa: E402

HIRING_SOURCE = "HIRING"
# caution_evidence 토큰(시스템 페널티 라벨). 한글 운영 라벨도 포함.
LOW_SAMPLE_TOKENS = ("insufficient_history", "low_base", "데이터 없음", "표본")
STALE_TOKENS = ("stale", "신선도", "오래")
SCORE_FLIP_EPS = 10.0   # final_score(0-100) day-over-day 변동이 이보다 크면 '급변'
RECENT_GAP_DAYS = 7     # 최근 N일 내 signal_date 공백 = 수집 중단 빨강 플래그


# ── JSONB 안전 변환 ───────────────────────────────────────────────────────────
def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ── 순수 지표 함수 (DB 무관, 단위테스트 대상) ─────────────────────────────────
def per_stock_series(signals: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """종목별 signal_date 오름차순 시계열."""
    by_stock: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        by_stock[s["stock_id"]].append(s)
    for rows in by_stock.values():
        rows.sort(key=lambda r: r["signal_date"])
    return dict(by_stock)


def flip_rate(series: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    """L2-①: 연속 signal_date 쌍 중 signal(방향)이 바뀐 비율 + score 평균 변동.

    pairs=0(시계열 1점 이하)면 측정 불가(rate=None)."""
    flips = pairs = 0
    deltas: list[float] = []
    for rows in series.values():
        for prev, cur in zip(rows, rows[1:]):
            pairs += 1
            if prev["signal"] != cur["signal"]:
                flips += 1
            if prev["final_score"] is not None and cur["final_score"] is not None:
                deltas.append(abs(float(cur["final_score"]) - float(prev["final_score"])))
    return {
        "flips": flips,
        "pairs": pairs,
        "rate": (flips / pairs) if pairs else None,
        "mean_score_delta": round(statistics.mean(deltas), 2) if deltas else None,
    }


def low_sample_ratio(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """L2-②: 저표본(insufficient_history/low_base/데이터없음/빈 breakdown) 신호 비율."""
    if not signals:
        return {"low": 0, "total": 0, "ratio": None}
    low = 0
    for s in signals:
        cautions = " ".join(str(c) for c in _as_list(s.get("caution_evidence")))
        breakdown = _as_dict(s.get("score_breakdown"))
        if (not breakdown) or any(tok in cautions for tok in LOW_SAMPLE_TOKENS):
            low += 1
    return {"low": low, "total": len(signals), "ratio": low / len(signals)}


def single_source_ratio(signals: list[dict[str, Any]]) -> dict[str, Any]:
    """L2-④: score_breakdown 이 단일 소스(HIRING 만/≤1개)인 신호 비율.

    breakdown 이 비어있으면(웜업) 단일소스로 보지 않고 별도(저표본)로 센다 — 과대평가 방지."""
    considered = [s for s in signals if _as_dict(s.get("score_breakdown"))]
    if not considered:
        return {"single": 0, "considered": 0, "ratio": None}
    single = sum(1 for s in considered if len(_as_dict(s["score_breakdown"])) <= 1)
    return {"single": single, "considered": len(considered), "ratio": single / len(considered)}


def freshness_days(signals: list[dict[str, Any]], as_of: date) -> int | None:
    """L2-⑤: 가장 최근 signal_date 로부터 경과일. 신호 없으면 None."""
    dates = [s["signal_date"] for s in signals if s.get("signal_date")]
    return (as_of - max(dates)).days if dates else None


def coverage_shock_days(coverage: list[dict[str, Any]]) -> list[str]:
    """L2-③: 일별 inserted_count 가 중앙값의 3배↑ 또는 1/3↓ 인 '커버리지 급변일'.

    파서 배포/소스 첫등장으로 수집량이 튀면 그날 score 급변은 진짜 신호가 아닌 아티팩트일 수 있다."""
    counts = [int(c["inserted"]) for c in coverage if c.get("inserted") is not None]
    if len(counts) < 3:
        return []
    med = statistics.median(counts)
    if med <= 0:
        return []
    shocks = []
    for c in coverage:
        n = c.get("inserted")
        if n is None:
            continue
        if n >= med * 3 or n <= med / 3:
            shocks.append(str(c["day"]))
    return shocks


def calibration_buckets(series: dict[int, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """L3: 직전 날 source_agreement(HIGH/MEDIUM/LOW) 버킷별 다음날 flip율.

    HIGH 가 LOW 보다 덜 뒤집혀야 confidence 가 보정(calibrated)됐다고 본다."""
    buckets: dict[str, dict[str, int]] = {b: {"flips": 0, "pairs": 0} for b in ("HIGH", "MEDIUM", "LOW")}
    for rows in series.values():
        for prev, cur in zip(rows, rows[1:]):
            agree = (prev.get("source_agreement") or "").upper()
            if agree not in buckets:
                continue
            buckets[agree]["pairs"] += 1
            if prev["signal"] != cur["signal"]:
                buckets[agree]["flips"] += 1
    return {
        b: {
            "flips": v["flips"],
            "pairs": v["pairs"],
            "rate": (v["flips"] / v["pairs"]) if v["pairs"] else None,
        }
        for b, v in buckets.items()
    }


def calibration_inverted(buckets: dict[str, dict[str, Any]]) -> bool | None:
    """HIGH flip율 > LOW flip율 이면 보정 붕괴(True). 표본 부족이면 None."""
    hi, lo = buckets.get("HIGH", {}).get("rate"), buckets.get("LOW", {}).get("rate")
    if hi is None or lo is None:
        return None
    return hi > lo


def composite_index(
    flip: dict[str, Any],
    low_sample: dict[str, Any],
    single: dict[str, Any],
    fresh: int | None,
    lookback: int,
) -> int | None:
    """L2 기반 합성 신뢰도지수 0~100(높을수록 신뢰). 핵심 입력(flip)이 측정불가면 None.

    안정성 0.45 + 표본충실 0.25 + 다중소스 0.15 + 신선도 0.15 가중."""
    if flip["rate"] is None:
        return None
    stability = 1.0 - flip["rate"]
    sample_ok = 1.0 - (low_sample["ratio"] or 0.0)
    multi_source = 1.0 - (single["ratio"] or 0.0)
    fresh_score = 1.0 if fresh is None else max(0.0, 1.0 - fresh / max(lookback, 1))
    idx = 100.0 * (0.45 * stability + 0.25 * sample_ok + 0.15 * multi_source + 0.15 * fresh_score)
    return round(idx)


def red_flags(
    signals: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    buckets: dict[str, dict[str, Any]],
    fresh: int | None,
    single: dict[str, Any],
    as_of: date,
) -> list[str]:
    """빨강 플래그: 수집 중단 / 단일소스 과다 / 보정 붕괴."""
    flags: list[str] = []
    if fresh is not None and fresh > RECENT_GAP_DAYS:
        flags.append(f"🚩 최근 {fresh}일간 신규 hiring signal_date 없음 — 수집 중단 의심")
    if single["ratio"] is not None and single["ratio"] > 0.8:
        flags.append(f"🚩 단일소스(HIRING) 의존 {single['ratio']:.0%} — 교차검증 부재")
    if calibration_inverted(buckets):
        flags.append("🚩 보정 붕괴 — HIGH agreement 가 LOW 보다 더 자주 뒤집힘(confidence miscalibrated)")
    return flags


def assemble_report(
    signals: list[dict[str, Any]], coverage: list[dict[str, Any]], as_of: date, lookback: int
) -> dict[str, Any]:
    """순수: 가져온 행 → 리포트 dict(스코어카드용). DB 무관이라 통째로 테스트 가능."""
    series = per_stock_series(signals)
    flip = flip_rate(series)
    low = low_sample_ratio(signals)
    single = single_source_ratio(signals)
    fresh = freshness_days(signals, as_of)
    buckets = calibration_buckets(series)
    conf = [float(s["confidence"]) for s in signals if s.get("confidence") is not None]
    flag_freq: Counter[str] = Counter()
    for s in signals:
        for c in _as_list(s.get("caution_evidence")):
            flag_freq[str(c)] += 1
    return {
        "as_of": as_of.isoformat(),
        "lookback": lookback,
        "n_signals": len(signals),
        "n_stocks": len(series),
        "L1": {
            "confidence_mean": round(statistics.mean(conf), 2) if conf else None,
            "agreement": dict(Counter((s.get("source_agreement") or "?") for s in signals)),
            "warning": dict(Counter((s.get("warning_level") or "?") for s in signals)),
            "needs_review_ratio": (
                round(sum(1 for s in signals if s.get("needs_review")) / len(signals), 3)
                if signals else None
            ),
            "caution_flags": dict(flag_freq.most_common()),
        },
        "L2": {
            "flip": flip,
            "low_sample": low,
            "single_source": single,
            "freshness_days": fresh,
            "coverage_shock_days": coverage_shock_days(coverage),
        },
        "L3_calibration": buckets,
        "composite_index": composite_index(flip, low, single, fresh, lookback),
        "red_flags": red_flags(signals, coverage, buckets, fresh, single, as_of),
    }


# ── DB 레이어 (READ-ONLY SELECT) ──────────────────────────────────────────────
async def fetch_signals(conn: Any, as_of: date, lookback: int) -> list[dict[str, Any]]:
    since = as_of - timedelta(days=lookback)
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (fs.stock_id, fs.signal_date)
            fs.stock_id, st.ticker, st.name, fs.signal_date, fs.signal,
            fs.final_score, fs.confidence, fs.consensus_score, fs.source_agreement,
            fs.warning_level, fs.needs_review, fs.caution_evidence, fs.score_breakdown
        FROM final_signals fs
        JOIN stocks st ON st.id = fs.stock_id
        WHERE fs.run_key = $1 AND fs.signal_date BETWEEN $2 AND $3
        ORDER BY fs.stock_id, fs.signal_date, fs.version DESC
        """,
        HIRING_SOURCE, since, as_of,
    )
    return [dict(r) for r in rows]


async def fetch_coverage(conn: Any, as_of: date, lookback: int) -> list[dict[str, Any]]:
    since = as_of - timedelta(days=lookback)
    rows = await conn.fetch(
        """
        SELECT started_at::date AS day, SUM(inserted_count) AS inserted
        FROM collector_runs
        WHERE collector_type = $1 AND started_at::date BETWEEN $2 AND $3
        GROUP BY 1 ORDER BY 1
        """,
        HIRING_SOURCE, since, as_of,
    )
    return [{"day": r["day"], "inserted": int(r["inserted"] or 0)} for r in rows]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    load_env()
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    import asyncpg

    as_of = date.fromisoformat(args.date) if args.date else date.today()
    conn_kwargs = parse_dsn(dsn)
    pool = await asyncpg.create_pool(
        **conn_kwargs, min_size=1, max_size=4,
        ssl=resolve_ssl(conn_kwargs["host"]), statement_cache_size=0,
    )
    try:
        async with pool.acquire() as conn:
            signals = await fetch_signals(conn, as_of, args.lookback)
            coverage = await fetch_coverage(conn, as_of, args.lookback)
    finally:
        await pool.close()
    return assemble_report(signals, coverage, as_of, args.lookback)


# ── 콘솔 스코어카드 ───────────────────────────────────────────────────────────
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def print_scorecard(rep: dict[str, Any]) -> None:
    print("\nHIRING SIGNAL RELIABILITY  (#298, read-only)")
    print(f"as_of={rep['as_of']}  lookback={rep['lookback']}d  "
          f"signals={rep['n_signals']}  stocks={rep['n_stocks']}")
    print("=" * 72)
    idx = rep["composite_index"]
    print(f"합성 신뢰도지수: {idx if idx is not None else 'n/a (표본 부족)'} / 100")
    print("-" * 72)
    l1 = rep["L1"]
    print("L1 자기보고")
    print(f"  confidence(mean)={l1['confidence_mean']}  agreement={l1['agreement']}")
    print(f"  warning={l1['warning']}  needs_review={_pct(l1['needs_review_ratio'])}")
    print(f"  caution_flags={l1['caution_flags']}")
    l2 = rep["L2"]
    f = l2["flip"]
    print("L2 독립 신뢰")
    print(f"  ① 안정성  signal flip율={_pct(f['rate'])} (flips {f['flips']}/{f['pairs']} pairs)"
          f"  mean|Δscore|={f['mean_score_delta']}")
    print(f"  ② 표본    저표본 비율={_pct(l2['low_sample']['ratio'])} "
          f"({l2['low_sample']['low']}/{l2['low_sample']['total']})")
    print(f"  ③ 아티팩트 커버리지 급변일={l2['coverage_shock_days'] or '없음'}")
    print(f"  ④ 단일소스 의존={_pct(l2['single_source']['ratio'])}")
    print(f"  ⑤ 신선도  최근 signal 후 경과={l2['freshness_days']}일")
    print("L3 보정 (agreement 버킷별 flip율 — HIGH가 LOW보다 낮아야 정상)")
    for b in ("HIGH", "MEDIUM", "LOW"):
        v = rep["L3_calibration"][b]
        print(f"  {b:<7} flip율={_pct(v['rate'])} ({v['flips']}/{v['pairs']})")
    print("-" * 72)
    if rep["red_flags"]:
        for fl in rep["red_flags"]:
            print(fl)
    else:
        print("✅ 빨강 플래그 없음")
    if rep["n_signals"] == 0 or (f["pairs"] or 0) == 0:
        print("ℹ️  시계열 표본 부족(웜업) — L2/L3 측정 불가. 며칠 누적 후 재실행.")


async def main() -> None:
    p = argparse.ArgumentParser(description="Hiring 신호 신뢰도 측정 (#298, read-only).")
    p.add_argument("--date", default=None, help="기준일 YYYY-MM-DD(기본 오늘)")
    p.add_argument("--lookback", type=int, default=30, help="lookback 일수(기본 30)")
    args = p.parse_args()
    print_scorecard(await run(args))


if __name__ == "__main__":
    asyncio.run(main())
