"""LOCAL DEV ONLY — 타임머신 합성 hiring 이력 주입 (cutover 측정 선검증).

⚠️⚠️ 가짜 채용 데이터를 raw_documents + hiring_raw_details 에 직접 INSERT 한다. ⚠️⚠️
실데이터 누적(스케줄러)을 기다리지 않고 *지금* 로컬에서 Warming-up 가드 해제·parity
(MATCH/MISMATCH 집계)를 end-to-end 검증하기 위한 개발용 도구다.

prod 오염 방지(footgun 차단):
  - DATABASE_URL host 가 로컬(localhost/127.0.0.1/postgres/db)이 아니면 **즉시 거부**.
  - 모든 합성 행은 external_id/source_hash 가 'MOCK_TM_' prefix → `--clean` 으로 일괄 제거.
  - 재실행하면 기존 MOCK 행을 먼저 지우고 새로 넣는다(멱등).

사용:
  cd services/agent-worker
  uv run python script/seed_historical_hiring.py          # 합성 6일치 주입
  uv run python script/seed_historical_hiring.py --clean  # 합성 데이터만 제거(실데이터 보존)

검증 흐름(주입 후):
  uv run python run_analyzers.py --hiring-only            # final_signals 가 NO_DATA 탈출하는지
  uv run python parity_hiring.py --date <today>           # MATCH/MISMATCH 가 실제로 나오는지
  uv run python script/seed_historical_hiring.py --clean  # 정리
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # repo 루트 .env → DATABASE_URL

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "postgres", "db", ""}
MOCK = "MOCK_TM_"          # 합성 행 식별 prefix (external_id / source_hash)
DAYS = 6                   # 오늘 포함 과거 N일치 (warming-up 가드 5일 해제 충분)

# 종목(ticker) → (회사명, 일별 추세). 추세로 방향을 다양화해 MATCH/MISMATCH 둘 다 유도.
TARGETS: dict[str, tuple[str, str]] = {
    "005930": ("삼성전자", "up"),
    "000660": ("SK하이닉스", "up"),
    "035420": ("NAVER", "flat"),
    "352820": ("HYBE", "down"),
    "005380": ("현대자동차", "up"),
}
SOURCES = ["PORTAL_UNKNOWN", "OFFICIAL_MOCK"]   # 소스 2종 → 웜업·혼합 판정 검증


def daily_count(trend: str, day_idx: int) -> int:
    """day_idx(0=가장 과거)에 따른 일별 공고 수. 추세로 상대강도/방향을 변화시킨다."""
    base = 5
    if trend == "up":
        return base + day_idx * 2          # 5,7,9,11,13,15
    if trend == "down":
        return max(1, base * 2 - day_idx * 2)  # 10,8,6,4,2,1
    return base                             # flat: 5


def main() -> int:
    ap = argparse.ArgumentParser(description="타임머신 합성 hiring 이력 (LOCAL ONLY)")
    ap.add_argument("--clean", action="store_true", help="합성 데이터(MOCK_TM_)만 제거")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("❌ DATABASE_URL 미설정")
        return 2
    host = urlparse(dsn).hostname or ""
    if host not in _LOCAL_HOSTS:
        print(f"❌ LOCAL ONLY — 비로컬 host '{host}' 거부(prod 오염 방지). "
              "로컬 Docker Postgres에서만 실행하세요.")
        return 2

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        # 기존 MOCK 제거(멱등) — raw_documents CASCADE 로 hiring_raw_details 동반 삭제.
        cur.execute("DELETE FROM raw_documents WHERE external_id LIKE %s", (MOCK + "%",))
        removed = cur.rowcount

        if args.clean:
            conn.commit()
            print(f"🧹 정리 완료 — MOCK 공고 {removed}건 제거(실데이터 보존).")
            return 0

        cur.execute("SELECT ticker, id FROM stocks WHERE ticker = ANY(%s)", (list(TARGETS),))
        ids = {t: i for t, i in cur.fetchall()}
        missing = [t for t in TARGETS if t not in ids]
        if missing:
            print(f"⚠️  stocks에 없는 ticker(스킵): {missing}")

        today = date.today()
        total = 0
        for ticker, (cname, trend) in TARGETS.items():
            sid = ids.get(ticker)
            if not sid:
                continue
            for day_idx in range(DAYS):
                d = today - timedelta(days=DAYS - 1 - day_idx)  # 과거→현재
                for src in SOURCES:
                    for k in range(daily_count(trend, day_idx)):
                        ext = f"{MOCK}{ticker}_{src}_{d.isoformat()}_{k}"
                        cur.execute(
                            "INSERT INTO raw_documents "
                            "(stock_id, source_type, source_name, external_id, source_hash, "
                            " title, published_at, collect_status) "
                            "VALUES (%s,'HIRING',%s,%s,%s,%s,%s,'success') RETURNING id",
                            (sid, src, ext, ext, f"[MOCK] {cname} {src} #{k}", d),
                        )
                        rid = cur.fetchone()[0]
                        cur.execute(
                            "INSERT INTO hiring_raw_details "
                            "(raw_document_id, stock_id, keyword, job_count, extra_payload, observed_date) "
                            "VALUES (%s,%s,'[MOCK]',1,%s,%s)",
                            (rid, sid, json.dumps({"source_type": src, "mock": True}), d),
                        )
                        total += 1
        conn.commit()
        print(f"✅ 합성 {total}건 주입 — {len(ids)}종목 × {len(SOURCES)}소스 × {DAYS}일 "
              f"(기존 MOCK {removed}건 교체). 날짜 {today - timedelta(days=DAYS-1)} ~ {today}.")
        print("   다음: run_analyzers.py --hiring-only → parity_hiring.py --date "
              f"{today.isoformat()} → --clean")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
