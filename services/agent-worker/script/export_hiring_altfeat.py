"""Step 2-①: 실 채용 공고수 기반 hiring alt-feature 생성기 (synthetic 음성대조군 교체).

vol-benchmark 의 `raw/hiring_altfeat.csv` 는 `tools/make_synth_hiring.py` 가 만든 **합성**이다
("실데이터 필요"의 음성대조군). 이 스크립트는 localhost DB 의 **실 jasoseol 공고수**(observed_date
기준 = KST 정규화 실 게시일 = point-in-time)를 집계해 **동일 변환**으로 `hiring_log/chg/roll_z` 를
만들어 교체한다. 변환식은 make_synth 와 1:1 동일(데이터만 실 → 공정한 ablation).

설계:
  - 추출: raw_documents ⋈ hiring_raw_details ⋈ stocks, source_type='HIRING' AND jasoseol, ANY(tickers).
    GROUP BY hiring_raw_details.observed_date (published_at::date 와 537/3586 건 +1일 차 — TZ 경계, observed_date 정확).
  - OHLCV 거래일 캘린더(date,ticker)에 매핑, 비공고일 0-fill.
  - shift 는 모델(cpu_lgbm_alt.py)이 적용 → 여기선 raw/unshifted.
  - 대상 = kospi20_ohlcv ∩ jasoseol 데이터 = 12종목.

Run (from services/agent-worker, .env 가 localhost 인지 확인):
    export DATABASE_URL=$(grep -E '^DATABASE_URL=' ../../.env | cut -d= -f2-)
    uv run python script/export_hiring_altfeat.py \
        --ohlcv ../../../vol-benchmark/raw/kospi20_ohlcv.csv \
        --out   ../../../vol-benchmark/raw/hiring_altfeat.csv
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg
import numpy as np
import pandas as pd

# kospi20_ohlcv ∩ jasoseol 데이터 = 12종목.
TARGET_TICKERS = [
    "000270", "000660", "005380", "005930", "012330", "015760",
    "028260", "032830", "035420", "068270", "207940", "373220",
]

# step 2-②: 스킬 피처. AI/ML 클러스터(사용자 가설 "AI/ML 채용 급증 ↔ 변동성").
AI_SKILLS = {"데이터분석", "ML", "TensorFlow", "PyTorch", "영상처리", "MLOps", "LLM", "sLM"}
SKILL_WINDOW = 126  # 거래일 ≈ 6개월(스킬 sparse 보정용 rolling 윈도우)


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(
            f"refusing to run: DATABASE_URL host is not local ({db_url.split('@')[-1]}). "
            "백테스트용 실데이터는 로컬 DB 에서만 추출한다."
        )


async def fetch_counts(db_url: str) -> pd.DataFrame:
    """observed_date×ticker 실 공고수(jasoseol). columns: date(str), ticker, posting_count."""
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT hrd.observed_date AS d, s.ticker AS ticker, count(*) AS posting_count
            FROM raw_documents rd
            JOIN hiring_raw_details hrd ON hrd.raw_document_id = rd.id
            JOIN stocks s ON s.id = rd.stock_id
            WHERE rd.source_type = 'HIRING'
              AND rd.source_url ILIKE '%jasoseol%'
              AND s.ticker = ANY($1::text[])
              AND hrd.observed_date IS NOT NULL
            GROUP BY hrd.observed_date, s.ticker
            """,
            TARGET_TICKERS,
        )
    finally:
        await conn.close()
    df = pd.DataFrame([(r["d"].strftime("%Y-%m-%d"), r["ticker"], int(r["posting_count"])) for r in rows],
                      columns=["date", "ticker", "posting_count"])
    return df


def load_calendar(ohlcv_path: Path) -> pd.DataFrame:
    """OHLCV 거래일 캘린더(date,ticker) — 대상 12종목만."""
    df = pd.read_csv(ohlcv_path, dtype={"ticker": str})
    df["ticker"] = df["ticker"].str.zfill(6)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[df["ticker"].isin(TARGET_TICKERS)]
    return df[["date", "ticker"]].drop_duplicates()


def transform(grid: pd.DataFrame) -> pd.DataFrame:
    """make_synth_hiring.py 와 1:1 동일 변환 (공정 ablation).

    hiring_log=log1p(s) · hiring_chg=pct_change.fillna(0) ·
    hiring_roll_z=((s-roll_mean)/roll_std), roll=rolling(20,min_periods=5).
    """
    grid = grid.sort_values(["ticker", "date"]).reset_index(drop=True)
    s = grid["posting_count"]
    grid["hiring_log"] = np.log1p(s)
    grid["hiring_chg"] = (
        grid.groupby("ticker")["posting_count"].pct_change()
        .replace([np.inf, -np.inf], np.nan).fillna(0.0)
    )
    roll = grid.groupby("ticker")["posting_count"].rolling(20, min_periods=5)
    roll_mean = roll.mean().reset_index(level=0, drop=True)
    roll_std = roll.std().replace(0.0, np.nan).reset_index(level=0, drop=True)
    grid["hiring_roll_z"] = ((s - roll_mean) / roll_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return grid[["date", "ticker", "hiring_log", "hiring_chg", "hiring_roll_z"]]


async def fetch_skills(db_url: str) -> pd.DataFrame:
    """공고별 ocr_skills 전개 → (date, ticker, skill). 빈 배열은 행 없음."""
    conn = await asyncpg.connect(db_url)
    try:
        rows = await conn.fetch(
            """
            SELECT hrd.observed_date AS d, s.ticker AS ticker,
                   jsonb_array_elements_text(hrd.ocr_skills::jsonb) AS skill
            FROM raw_documents rd
            JOIN hiring_raw_details hrd ON hrd.raw_document_id = rd.id
            JOIN stocks s ON s.id = rd.stock_id
            WHERE rd.source_type = 'HIRING' AND rd.source_url ILIKE '%jasoseol%'
              AND s.ticker = ANY($1::text[]) AND hrd.observed_date IS NOT NULL
            """,
            TARGET_TICKERS,
        )
    finally:
        await conn.close()
    return pd.DataFrame(
        [(r["d"].strftime("%Y-%m-%d"), r["ticker"], r["skill"]) for r in rows],
        columns=["date", "ticker", "skill"],
    )


def skill_features(calendar: pd.DataFrame, exploded: pd.DataFrame) -> pd.DataFrame:
    """거래일 그리드에 rolling 스킬 피처(점-인-타임, raw): skill_div·skill_ai_log·skill_ai_share.

    - 일별 (date,ticker): total_mentions·ai_mentions·skills(set).
    - SKILL_WINDOW(126 거래일) rolling: 합/AI비중/누적 고유스킬수(다양성). 스킬 sparse 보정.
    """
    exploded = exploded.copy()
    exploded["is_ai"] = exploded["skill"].isin(AI_SKILLS).astype(int)
    daily = exploded.groupby(["date", "ticker"]).agg(
        total=("skill", "size"),
        ai=("is_ai", "sum"),
        skills=("skill", lambda x: frozenset(x)),
    ).reset_index()

    g = calendar.merge(daily, on=["date", "ticker"], how="left").sort_values(["ticker", "date"])
    g["total"] = g["total"].fillna(0.0)
    g["ai"] = g["ai"].fillna(0.0)
    g["skills"] = g["skills"].apply(lambda v: v if isinstance(v, frozenset) else frozenset())

    out = []
    for tk, sub in g.groupby("ticker", sort=False):
        sub = sub.reset_index(drop=True)
        tot_roll = sub["total"].rolling(SKILL_WINDOW, min_periods=1).sum()
        ai_roll = sub["ai"].rolling(SKILL_WINDOW, min_periods=1).sum()
        sets = sub["skills"].tolist()
        div = []
        for i in range(len(sets)):
            lo = max(0, i - SKILL_WINDOW + 1)
            u: set = set()
            for s in sets[lo:i + 1]:
                u |= s
            div.append(len(u))
        sub["skill_div"] = div
        sub["skill_ai_log"] = np.log1p(ai_roll.to_numpy())
        sub["skill_ai_share"] = (ai_roll / (tot_roll + 1.0)).to_numpy()
        out.append(sub[["date", "ticker", "skill_div", "skill_ai_log", "skill_ai_share"]])
    return pd.concat(out, ignore_index=True)


async def main() -> None:
    ap = argparse.ArgumentParser(description="실 hiring 카운트 alt-feature 생성 (synthetic 교체)")
    ap.add_argument("--ohlcv", required=True, help="kospi20_ohlcv.csv 경로(거래일 캘린더)")
    ap.add_argument("--out", required=True, help="hiring_altfeat.csv 출력 경로")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        raise SystemExit("DATABASE_URL 필요(.env 로컬).")
    _require_local(db_url)

    calendar = load_calendar(Path(args.ohlcv))
    counts = await fetch_counts(db_url)
    print(f"DB 추출: {len(counts)}행(observed_date×ticker) · 캘린더 {len(calendar)}행(거래일×ticker)")

    merged = calendar.merge(counts, on=["date", "ticker"], how="left")
    merged["posting_count"] = merged["posting_count"].fillna(0)
    feats = transform(merged)

    # step 2-②: 스킬 피처 추가(rolling 다양성·AI 멘션·AI 비중).
    exploded = await fetch_skills(db_url)
    skfeat = skill_features(calendar, exploded)
    feats = feats.merge(skfeat, on=["date", "ticker"], how="left")
    print(f"스킬 전개 {len(exploded)}행 → 스킬 컬럼(skill_div·skill_ai_log·skill_ai_share) 병합")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_csv(out, index=False)

    nonzero = float((feats["hiring_log"] > 0).mean()) * 100.0
    ai_active = float((feats["skill_ai_share"] > 0).mean()) * 100.0
    print("=" * 60)
    print(f"wrote {out}: {feats['ticker'].nunique()} tickers, {len(feats)} rows, "
          f"{feats['date'].min()} → {feats['date'].max()}")
    print(f"비제로 공고일 {nonzero:.2f}% · skill_ai_share>0 거래일 {ai_active:.2f}% · "
          f"skill_div max {feats['skill_div'].max():.0f}")


if __name__ == "__main__":
    asyncio.run(main())
