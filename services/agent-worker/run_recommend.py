"""추천 라인 — ML/DL/LLM 라인 산출(final_signal + meta_signal)을 결정론 추천 점수로 랭킹.

worker-redesign 의 끝단 뒤에 붙는 **결정론 "주목·관심 추천"** 단계. 발행된 final_signal(5소스 종합
리포트)이 있으면 그 신호·확신을, 없으면 ML meta_signal(변동성/모델 신뢰도)을 근거로 점수를 매긴다.
**투자 조언이 아니다** — 매수/매도 지시 없이 발행 신호의 확신·안정성으로 줄을 세운다.

  recommendation_score = 100 · dir_w · conf_w · vol_w
    dir_w  = final_score/100 (발행 신호) | 0.5 (중립; 신호 없음)
    conf_w = 신뢰도 (final: confidence/100, meta: 모델 합의도 0..1)
    vol_w  = 1 / (1 + REC_VOL_PENALTY · combined_vol)   ← 변동성 역가중(안정적일수록 ↑)

  uv run python run_recommend.py                       # 전 활성 종목(신호 있는 것만) 랭킹
  uv run python run_recommend.py --tickers 005930,000660 --csv data/recommend.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))  # services/agent-worker

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets

bootstrap()

DEFAULT_RUN_KEY = "REC"
ML_RUN_KEY = "ML"
DISCLAIMER = "※ 추천은 발행 신호의 확신·안정성 기준 랭킹이며 투자 조언이 아닙니다(매수/매도 지시 없음)."


def _vol_penalty() -> float:
    raw = os.getenv("REC_VOL_PENALTY")
    return float(raw) if raw not in (None, "") else 5.0


def _clamp01(value: float) -> float:
    """0..1 가중치로 고정 — 상류(final_signals/meta_signals) CHECK 가 깨진 비정상 입력이 와도
    dir_w·conf_w 가 [0,1] 을 벗어나 recommendation_score 가 DB CHECK(0..100) 를 위반하지 않게."""
    return max(0.0, min(1.0, value))


def _num(value: Any) -> float | None:
    """asyncpg Decimal/None 안전 변환 — None/비수치는 None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_recommendation(
    *, final_row: dict[str, Any] | None, meta_row: dict[str, Any] | None, vol_penalty: float
) -> dict[str, Any]:
    """결정론 추천 점수 + 분해. final 우선, 없으면 meta(중립 방향)로 산출.

    단위: ``final_signals.final_score``·``confidence`` 는 0..100(NUMERIC) → /100 으로 0..1 정규화.
    ``meta_signals.confidence`` 는 이미 0..1(모델 합의도) → 그대로. 두 근거가 같은 0..1 스케일이라
    final/meta 추천 점수가 비교 가능하다. 모든 가중치는 _clamp01 로 [0,1] 보장.
    """
    if final_row is not None:
        basis = "final"
        signal = final_row.get("signal")
        final_score = _num(final_row.get("final_score"))          # 0..100
        confidence_raw = _num(final_row.get("confidence"))        # 0..100
        dir_w = _clamp01((final_score if final_score is not None else 50.0) / 100.0)
        conf_w = _clamp01((confidence_raw if confidence_raw is not None else 0.0) / 100.0)
    else:
        basis = "meta"
        signal = None
        final_score = None
        dir_w = 0.5                                               # 발행 신호 없음 → 방향 중립
        conf_w = _clamp01(_num(meta_row.get("confidence")) or 0.0) if meta_row else 0.0  # 이미 0..1

    combined_vol = _num(meta_row.get("combined_vol")) if meta_row else None
    # combined_vol 은 변동성(≥0). 음수 비정상값은 0 으로 바닥 처리해 vol_w 가 1 을 넘지 않게.
    vol_w = 1.0 / (1.0 + vol_penalty * max(combined_vol, 0.0)) if combined_vol is not None else 1.0
    # dir_w·conf_w·vol_w ∈ [0,1] 이므로 score ∈ [0,100] 보장(DB CHECK 안전).
    score = round(100.0 * dir_w * conf_w * vol_w, 2)
    return {
        "basis": basis,
        "signal": signal,
        "final_score": round(final_score, 2) if final_score is not None else None,
        "confidence": round(conf_w, 4),
        "combined_vol": combined_vol,
        "recommendation_score": score,
        "components": {
            "dir_w": round(dir_w, 4),
            "conf_w": round(conf_w, 4),
            "vol_w": round(vol_w, 4),
            "vol_penalty": vol_penalty,
            "basis": basis,
        },
    }


async def run(args: argparse.Namespace) -> None:
    load_env()
    from signal_alpha_data_access.repositories import (
        MetaSignalRepository,
        RecommendationRepository,
        SignalRepository,
    )

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    vol_penalty = _vol_penalty()
    pool = await build_pool(max_pool_size=4)
    try:
        async with pool.acquire() as conn:
            targets = await resolve_targets(conn, tickers=tickers, limit=None)
            if tickers:
                found = {t["ticker"] for t in targets}
                missing = [t for t in tickers if t not in found]
                if missing:
                    print(f"⚠️  unknown tickers (skipped): {', '.join(missing)}")
            if not targets:
                raise SystemExit("No target stocks resolved.")

            stock_ids = [int(t["stock_id"]) for t in targets]
            by_id = {int(t["stock_id"]): t for t in targets}
            signals = SignalRepository(conn)
            meta_repo = MetaSignalRepository(conn)

            # 발행 current final_signal — 종목당 첫 행(=current). 리포지토리 계층 재사용.
            # 의도: list_current_by_stock_ids 는 is_published=TRUE 만 반환한다. 게이트가 보류한
            # 미발행/needs_review 신호는 추천하지 않고(가드레일), meta 가 있으면 meta 근거로 강등된다.
            final_by_stock: dict[int, dict[str, Any]] = {}
            for row in await signals.list_current_by_stock_ids(stock_ids):
                final_by_stock.setdefault(int(row["stock_id"]), dict(row))

            scored: list[dict[str, Any]] = []
            asof_dates: list[date] = []
            for stock_id in stock_ids:
                final_row = final_by_stock.get(stock_id)
                meta_raw = await meta_repo.latest_for_stock(stock_id=stock_id, run_key=ML_RUN_KEY)
                meta_row = dict(meta_raw) if meta_raw else None
                if final_row is None and meta_row is None:
                    # 의도: 활성(resolve_targets is_active=TRUE) 종목 중 발행 신호·ML meta 가 둘 다
                    # 없으면 추천 근거가 없으므로 제외. 상장폐지(비활성)·근거없음은 추천하지 않는다.
                    continue
                result = score_recommendation(
                    final_row=final_row, meta_row=meta_row, vol_penalty=vol_penalty
                )
                if meta_row and meta_row.get("asof_date"):
                    asof_dates.append(meta_row["asof_date"])
                scored.append({"stock_id": stock_id, **by_id[stock_id], **result})

            if not scored:
                raise SystemExit("No stock has a final_signal or meta_signal yet — run the pipeline first.")

            # 결정론 랭킹: 점수 내림차순, 동점은 stock_id 오름차순.
            scored.sort(key=lambda r: (-r["recommendation_score"], r["stock_id"]))
            asof = max(asof_dates) if asof_dates else date.today()

            # 랭킹 전체를 한 트랜잭션으로 — 중간 실패 시 부분 순위가 남지 않게(원자적 교체).
            rec_repo = RecommendationRepository(conn)
            async with conn.transaction():
                for rank, row in enumerate(scored, start=1):
                    row["rank"] = rank
                    await rec_repo.upsert_recommendation(
                        stock_id=row["stock_id"],
                        asof_date=asof,
                        run_key=args.run_key,
                        rank=rank,
                        recommendation_score=row["recommendation_score"],
                        basis=row["basis"],
                        signal=row["signal"],
                        final_score=row["final_score"],
                        confidence=row["confidence"],
                        combined_vol=row["combined_vol"],
                        components=row["components"],
                    )

        _print_table(scored, asof=asof, vol_penalty=vol_penalty)
        _write_csv(scored, asof=asof, csv_path=args.csv)
    finally:
        await pool.close()


def _print_table(scored: list[dict[str, Any]], *, asof: Any, vol_penalty: float) -> None:
    print("RECOMMENDATION LINE")
    print("=" * 72)
    print(f"asof={asof}  vol_penalty={vol_penalty}  ranked={len(scored)}")
    header = f"{'#':>2}  {'ticker':<8}{'name':<12}{'score':>7}{'basis':>7}{'signal':>9}{'fscore':>8}{'conf':>7}{'comb_vol':>10}"
    print(header)
    print("-" * len(header))
    for row in scored:
        fscore = f"{row['final_score']:>8.1f}" if row["final_score"] is not None else f"{'—':>8}"
        conf = f"{row['confidence']:>7.2f}" if row["confidence"] is not None else f"{'—':>7}"
        cvol = f"{row['combined_vol']:>10.4f}" if row["combined_vol"] is not None else f"{'—':>10}"
        name = (row.get("name") or "")[:11]
        print(f"{row['rank']:>2}  {row['ticker']:<8}{name:<12}{row['recommendation_score']:>7.2f}"
              f"{row['basis']:>7}{str(row['signal'] or '—'):>9}{fscore}{conf}{cvol}")
    print("-" * len(header))
    print(DISCLAIMER)


def _write_csv(scored: list[dict[str, Any]], *, asof: Any, csv_path: str | None) -> None:
    path = Path(csv_path) if csv_path else Path("data") / f"recommendations_{asof}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["rank", "ticker", "name", "recommendation_score", "basis", "signal",
                  "final_score", "confidence", "combined_vol"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(scored)
    print(f"\n📊 {len(scored)} recommendations  →  {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic recommendation ranking over final/meta signals.")
    parser.add_argument("--tickers", help="Comma-separated tickers (default: all active stocks with a signal).")
    parser.add_argument("--run-key", default=DEFAULT_RUN_KEY, help="recommendations.run_key (default REC).")
    parser.add_argument("--csv", help="CSV path (default data/recommendations_<asof>.csv).")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
