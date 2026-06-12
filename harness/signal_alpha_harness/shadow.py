"""포워드 섀도 테스트 — 백테스트보다 강한 증거 (설계 원칙 4).

매일 장 마감 후 점수를 **결과가 나오기 전에** 타임스탬프와 함께 append-only
JSONL에 기록하고, 20영업일이 지나면 실제 수익률과 대조한다. 기록 시점에
미래가 존재하지 않으므로 백테스트 편향이 구조적으로 0%다.

Usage (from harness/):

    uv run python -m signal_alpha_harness.shadow --record    # 당일 점수 기록 (~3분)
    uv run python -m signal_alpha_harness.shadow --evaluate  # 경과분 대조 리포트

기록 파일(data/shadow/predictions.jsonl)은 git에 커밋한다 — 커밋 해시가
"기록이 사후 수정되지 않았다"는 증거가 된다. 같은 trade_date의 중복 기록은
거부된다 (idempotent — 작업 스케줄러가 하루 두 번 돌아도 안전).

Windows 작업 스케줄러 등록 예 (매 영업일 17:00):
    schtasks /Create /TN SignalAlphaShadow /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
      /ST 17:00 /TR "cmd /c cd /d C:\\Users\\biop9\\signal-alpha\\harness && python -m uv run python -m signal_alpha_harness.shadow --record"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from signal_alpha_harness.combine import ACTIVE_FACTORS, add_combined_score
from signal_core.quant.confidence import add_confidence
from signal_alpha_harness.universe import DATA_DIR, load_universe

SHADOW_PATH = DATA_DIR / "shadow" / "predictions.jsonl"
FUNDAMENTALS_PATH = DATA_DIR / "fundamentals_kospi200.parquet"
HORIZON = 20
LOOKBACK_CALENDAR_DAYS = 300  # 저변동 60영업일 + 워밍업 여유
FUNDAMENTALS_STALE_DAYS = 21


def fetch_recent_panel(lookback_days: int = LOOKBACK_CALENDAR_DAYS, pause_sec: float = 0.3) -> pd.DataFrame:
    """유니버스 전 종목의 최근 일봉 미니 패널 (수급은 결측 — 팩터 미사용)."""
    from pykrx import stock as krx

    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=lookback_days)
    frames = []
    for stock_item in load_universe():
        ohlcv = krx.get_market_ohlcv(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), stock_item.ticker
        )
        time.sleep(pause_sec)
        frame = ohlcv.rename(
            columns={"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
        )[["open", "high", "low", "close", "volume"]]
        frame.index.name = "trade_date"
        frame = frame.reset_index()
        frame["ticker"] = stock_item.ticker
        frame["name"] = stock_item.name
        frame["foreign_net"] = pd.NA
        frame["institution_net"] = pd.NA
        frames.append(frame)
    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    return panel.sort_values(["ticker", "trade_date"]).reset_index(drop=True)


def score_latest(panel: pd.DataFrame, fundamentals: pd.DataFrame | None) -> pd.DataFrame:
    """미니 패널 → 최신 거래일의 점수·확신도 행들."""
    scored = add_combined_score(panel, fundamentals)
    scored = add_confidence(scored, total_factors=len(ACTIVE_FACTORS))
    latest = scored["trade_date"].max()
    return scored[scored["trade_date"] == latest]


def recorded_trade_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    dates = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                dates.add(json.loads(line)["trade_date"])
    return dates


def append_predictions(path: Path, day_rows: pd.DataFrame) -> int:
    """최신 거래일 점수를 append. 이미 기록된 trade_date면 0을 반환(거부)."""
    trade_date = day_rows["trade_date"].max().strftime("%Y-%m-%d")
    if trade_date in recorded_trade_dates(path):
        return 0
    predicted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a", encoding="utf-8") as handle:
        for _, row in day_rows.iterrows():
            record = {
                "predicted_at": predicted_at,
                "trade_date": trade_date,
                "ticker": str(row["ticker"]),
                "score": None if pd.isna(row["score"]) else round(float(row["score"]), 2),
                "confidence": str(row["confidence"]),
                "n_factors_used": int(row["n_factors_used"]),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    return written


def evaluate_records(
    predictions: pd.DataFrame, closes: pd.DataFrame, horizon: int = HORIZON
) -> pd.DataFrame:
    """기록된 예측과 실제 수익률 대조 — 일별 섀도 IC.

    closes: trade_date×ticker close 패널 (평가 시점에 새로 수집한 것).
    horizon 영업일이 아직 안 지난 예측일은 제외된다.
    """
    from signal_alpha_harness.metrics import daily_spearman_ic

    price = closes.pivot_table(index="trade_date", columns="ticker", values="close", aggfunc="first")
    price = price.sort_index()
    forward = price.shift(-horizon) / price - 1.0

    rows = []
    for trade_date, group in predictions.dropna(subset=["score"]).groupby("trade_date"):
        date = pd.Timestamp(trade_date)
        if date not in forward.index:
            continue
        realized = forward.loc[date]
        merged = group.set_index("ticker")["score"].to_frame()
        merged["fwd"] = merged.index.map(realized)
        merged = merged.dropna()
        if len(merged) < 5:
            continue
        ic = daily_spearman_ic(
            merged["score"].to_numpy(dtype=float).reshape(1, -1),
            merged["fwd"].to_numpy(dtype=float).reshape(1, -1),
        )[0]
        rows.append({"trade_date": trade_date, "n": int(len(merged)), "shadow_ic": float(ic)})
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="포워드 섀도 테스트")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--path", type=Path, default=SHADOW_PATH)
    args = parser.parse_args(argv)

    if args.record == args.evaluate:
        parser.error("--record 또는 --evaluate 중 하나")

    fundamentals = pd.read_parquet(FUNDAMENTALS_PATH) if FUNDAMENTALS_PATH.exists() else None
    if fundamentals is not None and FUNDAMENTALS_PATH.exists():
        age_days = (time.time() - FUNDAMENTALS_PATH.stat().st_mtime) / 86400
        if age_days > FUNDAMENTALS_STALE_DAYS:
            print(f"warning: 재무 데이터가 {age_days:.0f}일 경과 — collect_fundamentals 재실행 권장")

    if args.record:
        panel = fetch_recent_panel()
        day_rows = score_latest(panel, fundamentals)
        written = append_predictions(args.path, day_rows)
        latest = day_rows["trade_date"].max().date()
        if written == 0:
            print(f"{latest}: 이미 기록됨 — 스킵 (append-only 보존)")
        else:
            withheld = int(day_rows["score"].isna().sum())
            print(f"{latest}: {written}종목 기록 (점수 보류 {withheld}건) -> {args.path}")
            print("git 커밋을 잊지 마세요 — 커밋 해시가 사후 수정 불가의 증거입니다.")
        return 0

    if not args.path.exists():
        print("기록이 없습니다 — --record를 먼저 실행하세요", file=sys.stderr)
        return 1
    predictions = pd.read_json(args.path, lines=True)
    predictions["trade_date"] = pd.to_datetime(predictions["trade_date"]).dt.strftime("%Y-%m-%d")
    panel = fetch_recent_panel(lookback_days=LOOKBACK_CALENDAR_DAYS + HORIZON * 2)
    report = evaluate_records(predictions, panel)
    if report.empty:
        print(f"아직 {HORIZON}영업일이 경과한 예측이 없습니다 "
              f"(기록 {predictions['trade_date'].nunique()}일치)")
        return 0
    print(report.to_string(index=False))
    print(f"\n섀도 IC 평균 {report['shadow_ic'].mean():+.4f} ({len(report)}일) — "
          f"백테스트 IC(+0.0466)와 비교하세요")
    return 0


if __name__ == "__main__":
    sys.exit(main())
