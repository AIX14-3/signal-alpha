"""메타러너 stacking 가중 학습 하네스 — 합성 데이터로 "배관"(누수 차단·정규화·가드) 검증.

흐름:
  ① 게이트 모델(EWMA/HAR-RV/GARCH/LightGBM)을 기존 walk-forward 하네스
     (``vol_models.common.harness._ticker_rows``)로 돌려 **OOF 예측 + 실현변동성**을 만든다.
     (각 예측은 asof≤t 데이터만, 타깃은 미래 수익률만 — 누수 없음.)
  ② OOF 행을 **시간순**으로 train/hold-out 분할(랜덤 k-fold 금지; meta-learner-training.md 규율).
  ③ 후보 stacking 가중을 train 에 적합(equal·nnls·ridge), hold-out QLIKE 로 검증.
  ④ 가드: 학습 우승이 equal 을 hold-out 에서 실제로 이기고(min_improve), train↔hold 격차·가중
     집중도가 한도 이내일 때만 ``meta_learner.json`` 을 쓴다. 아니면 equal 유지(과적합 산출물 차단).

합성 OHLCV 는 모델 간 구조차가 없어 보통 equal 이 유지된다 — **가드가 작동한다는 신호**다.
실제 가중 선택은 실 OHLCV 백필 후에 의미가 있다.

  uv run python tools/harness/train_meta.py --warmup 300 --step 3 \
      --artifact tools/harness/out/meta_learner.json --leaderboard tools/harness/out/meta_leaderboard.csv
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import nnls

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "services" / "agent-worker"))

from mvp_runtime import bootstrap, build_pool, load_env, resolve_targets  # noqa: E402

bootstrap()

from app.ml.contract_adapter import build_frame  # noqa: E402
from app.ml.model_registry import resolve_models  # noqa: E402
from vol_models.common.harness import _ticker_rows  # noqa: E402


# ---- OOF 생성 -------------------------------------------------------------

def _synthetic_frames(n: int, sessions: int, seed: int) -> dict[str, pd.DataFrame]:
    """DB 없이 인메모리 GBM 합성 종목 — 시드별 독립 데이터(병렬 설계 탐색용, DB 경합 없음)."""
    from seed_synthetic_ohlcv import synth_ohlcv

    frames: dict[str, pd.DataFrame] = {}
    for i in range(n):
        ticker = f"SYN{i:03d}"
        df = synth_ohlcv(ticker=ticker, sessions=sessions, end=date(2026, 1, 1), base_seed=seed)
        frames[ticker] = build_frame(df.to_dict("records"), ticker=ticker)
    return frames


async def _load_frames(pool: Any, tickers: list[str] | None, lookback: int) -> dict[str, pd.DataFrame]:
    from signal_alpha_data_access.repositories import MarketDataRepository

    frames: dict[str, pd.DataFrame] = {}
    async with pool.acquire() as conn:
        targets = await resolve_targets(conn, tickers=tickers, limit=None)
        market = MarketDataRepository(conn)
        for target in targets:
            records = await market.list_recent_ohlcv(stock_id=int(target["stock_id"]), limit=lookback)
            rows = [dict(r) for r in records]
            if len(rows) < 60:
                continue
            frames[target["ticker"]] = build_frame(rows, ticker=target["ticker"])
    return frames


def build_oof(
    frames: dict[str, pd.DataFrame], *, horizon: int, warmup: int, step: int, seed: int
) -> tuple[pd.DataFrame, list[str]]:
    """모델×종목 walk-forward → (ticker, asof_date) × 모델 pred_vol + actual_vol 와이드 표."""
    specs = resolve_models()
    models = [s.name for s in specs]
    records: list[dict[str, Any]] = []
    for spec in specs:
        predict = spec.load()
        for ticker, frame in frames.items():
            records.extend(
                _ticker_rows(predict, spec.name, frame, ticker, horizon, warmup, step, seed, dict(spec.cfg))
            )
    if not records:
        return pd.DataFrame(), models
    df = pd.DataFrame.from_records(records)
    wide = df.pivot_table(index=["ticker", "asof_date"], columns="model", values="pred_vol")
    actual = df.groupby(["ticker", "asof_date"])["actual_vol"].first()
    wide = wide.join(actual).dropna()  # 모든 모델이 예측한 행만(정렬된 stacking 입력)
    present = [m for m in models if m in wide.columns]
    return wide.reset_index().sort_values("asof_date").reset_index(drop=True), present


# ---- 후보 가중 적합 + 평가 ------------------------------------------------

def _ridge_nnls(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    """비음수 ridge — X 에 sqrt(alpha)·I, y 에 0 을 덧붙여 nnls(L2 + ≥0)."""
    k = x.shape[1]
    x_aug = np.vstack([x, np.sqrt(alpha) * np.eye(k)])
    y_aug = np.concatenate([y, np.zeros(k)])
    w, _ = nnls(x_aug, y_aug)
    return w


def fit_candidates(x: np.ndarray, y: np.ndarray, models: list[str]) -> dict[str, dict[str, float]]:
    n = len(models)
    cands: dict[str, dict[str, float]] = {"equal": {m: 1.0 / n for m in models}}
    w_nnls, _ = nnls(x, y)
    cands["nnls"] = dict(zip(models, w_nnls.tolist()))
    for alpha in (0.01, 0.1):
        cands[f"ridge{alpha}"] = dict(zip(models, _ridge_nnls(x, y, alpha).tolist()))
    return cands


def _normalized(weights: dict[str, float], models: list[str]) -> np.ndarray:
    w = np.array([max(weights.get(m, 0.0), 0.0) for m in models], dtype=float)
    s = w.sum()
    return np.ones(len(models)) / len(models) if s <= 0 else w / s


def _qlike(pred: np.ndarray, actual: np.ndarray) -> float:
    """변동성 예측 손실(QLIKE, 분산 기준): a²/p² - ln(a²/p²) - 1 ≥ 0. 낮을수록 좋음."""
    p2 = np.maximum(pred, 1e-8) ** 2
    a2 = np.maximum(actual, 1e-8) ** 2
    r = a2 / p2
    return float(np.mean(r - np.log(r) - 1.0))


def _rmse(pred: np.ndarray, actual: np.ndarray) -> float:
    return float(np.sqrt(np.mean((pred - actual) ** 2)))


def evaluate(
    cands: dict[str, dict[str, float]],
    *,
    x_tr: np.ndarray, y_tr: np.ndarray, x_ho: np.ndarray, y_ho: np.ndarray, models: list[str],
) -> list[dict[str, Any]]:
    board: list[dict[str, Any]] = []
    for name, weights in cands.items():
        w = _normalized(weights, models)
        tr_q = _qlike(x_tr @ w, y_tr)
        ho_q = _qlike(x_ho @ w, y_ho)
        board.append({
            "method": name,
            "train_qlike": round(tr_q, 6),
            "holdout_qlike": round(ho_q, 6),
            "holdout_rmse": round(_rmse(x_ho @ w, y_ho), 6),
            "gap": round(ho_q - tr_q, 6),
            "max_weight": round(float(w.max()), 4),
            "weights": {m: round(float(wi), 6) for m, wi in zip(models, w)},
        })
    return sorted(board, key=lambda r: r["holdout_qlike"])


def select_winner(
    board: list[dict[str, Any]], *, min_improve: float, gap_cap: float, conc_cap: float
) -> tuple[str, str]:
    """가드 통과 시 학습 우승, 아니면 equal. (winner, reason) 반환."""
    by = {r["method"]: r for r in board}
    equal_q = by["equal"]["holdout_qlike"]
    learned = [r for r in board if r["method"] != "equal"]
    best = min(learned, key=lambda r: r["holdout_qlike"]) if learned else None
    if best is None:
        return "equal", "no_learned_candidate"
    if best["holdout_qlike"] >= equal_q * (1.0 - min_improve):
        return "equal", f"learned_did_not_beat_equal(best={best['method']} {best['holdout_qlike']:.4f} vs equal {equal_q:.4f})"
    if best["gap"] > gap_cap:
        return "equal", f"overfit_gap({best['method']} gap={best['gap']:.4f}>{gap_cap})"
    if best["max_weight"] > conc_cap:
        return "equal", f"weight_concentration({best['method']} max={best['max_weight']}>{conc_cap})"
    return best["method"], "adopted"


# ---- 실행 -----------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    if args.synthetic:
        frames = _synthetic_frames(args.synthetic, args.sessions, args.seed)
    else:
        load_env()
        pool = await build_pool(max_pool_size=4)
        try:
            frames = await _load_frames(pool, tickers, args.lookback)
        finally:
            await pool.close()
    if not frames:
        raise SystemExit("No OHLCV frames (seed synthetic OHLCV or pass --synthetic N).")

    quiet = args.quiet or bool(args.result_json)
    with contextlib.redirect_stdout(io.StringIO()) if quiet else contextlib.nullcontext():
        oof, models = build_oof(frames, horizon=args.horizon, warmup=args.warmup, step=args.step, seed=args.seed)
    if oof.empty or len(models) < 2:
        raise SystemExit(f"Insufficient OOF rows/models (rows={len(oof)} models={models}). Lower --warmup/--step.")

    # 시간순 분할 — asof_date 기준 train/hold-out(누수 차단).
    cut = oof["asof_date"].quantile(args.train_frac, interpolation="lower")
    train, holdout = oof[oof["asof_date"] <= cut], oof[oof["asof_date"] > cut]
    if len(train) < len(models) + 2 or len(holdout) < 2:
        raise SystemExit(f"Too few rows after time split (train={len(train)} holdout={len(holdout)}).")

    x_tr, y_tr = train[models].to_numpy(float), train["actual_vol"].to_numpy(float)
    x_ho, y_ho = holdout[models].to_numpy(float), holdout["actual_vol"].to_numpy(float)

    cands = fit_candidates(x_tr, y_tr, models)
    board = evaluate(cands, x_tr=x_tr, y_tr=y_tr, x_ho=x_ho, y_ho=y_ho, models=models)
    winner, reason = select_winner(
        board, min_improve=args.min_improve, gap_cap=args.gap_cap, conc_cap=args.conc_cap
    )

    # 병렬 탐색(Workflow) 모드: 사람용 출력·아티팩트 대신 구조화 결과만 기록(에이전트 파싱용).
    if args.result_json:
        Path(args.result_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_json).write_text(
            json.dumps(
                {
                    "seed": args.seed,
                    "synthetic": bool(args.synthetic),
                    "synthetic_n": int(args.synthetic or 0),
                    "sessions": int(args.sessions),
                    "models": models,
                    "oof_rows": int(len(oof)),
                    "train": int(len(train)),
                    "holdout": int(len(holdout)),
                    "winner": winner,
                    "reason": reason,
                    "board": board,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return

    _print_board(board, oof=oof, models=models, train=len(train), holdout=len(holdout), winner=winner, reason=reason)

    # leaderboard 항상 기록.
    lb_path = Path(args.leaderboard)
    lb_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{k: v for k, v in r.items() if k != "weights"} for r in board]).to_csv(lb_path, index=False)
    print(f"\n📊 leaderboard → {lb_path}")

    # 가드 통과(또는 --force) 시에만 meta_learner.json 기록 — 양수 가중만.
    if winner != "equal" or args.force:
        win_row = next(r for r in board if r["method"] == winner)
        weights = {m: w for m, w in win_row["weights"].items() if w > 0.0}
        artifact = {
            "weights": weights,
            "method": winner,
            "trained_on": "synthetic" if not tickers else "synthetic_subset",
            "selected_reason": reason,
            "metrics": {k: win_row[k] for k in ("train_qlike", "holdout_qlike", "holdout_rmse", "gap", "max_weight")},
            "models": models,
            "oof_rows": int(len(oof)),
        }
        art_path = Path(args.artifact)
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📦 meta_learner.json ({winner}) → {art_path}")
    else:
        print(f"🛡  equal 유지(가드): {reason} — meta_learner.json 미기록(equal_fallback 보존).")


def _print_board(board, *, oof, models, train, holdout, winner, reason) -> None:
    print("META-LEARNER TRAINING HARNESS")
    print("=" * 72)
    print(f"models={models}  oof_rows={len(oof)}  train={train}  holdout={holdout}")
    hdr = f"{'method':<10}{'train_QLIKE':>13}{'holdout_QLIKE':>15}{'gap':>9}{'max_w':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in board:
        mark = " ←" if r["method"] == winner else ""
        print(f"{r['method']:<10}{r['train_qlike']:>13.5f}{r['holdout_qlike']:>15.5f}{r['gap']:>9.5f}{r['max_weight']:>8.3f}{mark}")
    print("-" * len(hdr))
    print(f"winner={winner}  ({reason})")


def main() -> None:
    p = argparse.ArgumentParser(description="Train meta-learner stacking weights (walk-forward OOF).")
    p.add_argument("--tickers", help="Comma-separated (default: all seeded active stocks).")
    p.add_argument("--synthetic", type=int, metavar="N", help="In-memory GBM mode: N synthetic tickers, no DB (parallel search).")
    p.add_argument("--sessions", type=int, default=500, help="Sessions per synthetic ticker (--synthetic).")
    p.add_argument("--result-json", dest="result_json", help="Write structured result JSON here (suppresses human output).")
    p.add_argument("--quiet", action="store_true", help="Silence walk-forward progress output.")
    p.add_argument("--lookback", type=int, default=500, help="OHLCV sessions per ticker (DB mode).")
    p.add_argument("--horizon", type=int, default=10)
    p.add_argument("--warmup", type=int, default=300, help="Warmup sessions before first OOF origin.")
    p.add_argument("--step", type=int, default=3, help="OOF forecast cadence (days).")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--train-frac", type=float, default=0.6, dest="train_frac", help="Time-split train fraction.")
    p.add_argument("--min-improve", type=float, default=0.02, dest="min_improve", help="Min holdout QLIKE improvement vs equal to adopt.")
    p.add_argument("--gap-cap", type=float, default=0.5, dest="gap_cap", help="Max train↔holdout QLIKE gap.")
    p.add_argument("--conc-cap", type=float, default=0.85, dest="conc_cap", help="Max single-model weight.")
    p.add_argument("--artifact", default=str(ROOT / "tools" / "harness" / "out" / "meta_learner.json"))
    p.add_argument("--leaderboard", default=str(ROOT / "tools" / "harness" / "out" / "meta_leaderboard.csv"))
    p.add_argument("--force", action="store_true", help="Write artifact even if equal wins (demo).")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
