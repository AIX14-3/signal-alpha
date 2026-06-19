#!/usr/bin/env python3
"""hiring 일별 적재 신선도 점검 (#297 catch-up·검증 공용, 크로스플랫폼).

collector_runs(권위 소스)에서 최신 성공/부분성공 HIRING run의 KST 날짜를 오늘(KST)과 비교한다.
실패한 run은 무시(success/partial만) — 실패 run이 catch-up을 오인 종료시키지 않게.

종료 코드:
  0  오늘(KST) 적재됨        → (catch-up) 이미 완료 → 래퍼는 skip
  1  오늘 없음(stale)         → (catch-up) 실행 필요 / (검증) 적재 실패
  2  DB 접속/조회 불가        → 래퍼는 '판단 불가'로 보고 진행(실행을 막지 않음)

사용:
  uv run python ops/check_hiring_freshness.py            # 사람이 보기(상태 출력)
  uv run python ops/check_hiring_freshness.py --quiet    # 종료코드만(래퍼 catch-up 판정)
  uv run python ops/check_hiring_freshness.py --stale-days 2
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# repo 루트 .env → DATABASE_URL (ops/ → agent-worker → services → repo)
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(os.environ.get("ENV_FILE") or (_REPO_ROOT / ".env"))

_KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="출력 없이 종료코드만")
    ap.add_argument("--stale-days", type=int, default=2, help="이 일수 이상 지나면 stale 경고")
    args = ap.parse_args()

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        say("⚠️  DATABASE_URL 미설정 — 판단 불가")
        return 2

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(dsn, echo=False, future=True)
        try:
            with engine.connect() as conn:
                latest = conn.execute(text(
                    "SELECT max(started_at) FROM collector_runs "
                    "WHERE collector_type = 'HIRING' AND status IN ('success', 'partial')"
                )).scalar()
        finally:
            engine.dispose()
    except Exception as exc:
        say(f"⚠️  DB 조회 실패 — 판단 불가: {exc}")
        return 2

    today = datetime.now(_KST).date()
    if latest is None:
        say("❌ 성공한 HIRING 수집 기록이 없음 — 아직 한 번도 안 돎")
        return 1

    latest_kst = latest.astimezone(_KST).date()
    age = (today - latest_kst).days
    if latest_kst >= today:
        say(f"✅ 오늘({today}) 적재됨 — 최신 성공 run {latest_kst}")
        return 0

    flag = " 🚨 STALE" if age >= args.stale_days else ""
    say(f"❌ 오늘 적재 없음 — 최신 성공 run {latest_kst} ({age}일 전){flag}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
