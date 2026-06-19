#!/usr/bin/env bash
# 일별 채용 수집·분석 자동 실행 래퍼 (#297).
# cron은 이 스크립트만 호출 — 로직을 한곳에 모아 수동 실행도 동일하게 보호한다.
# 설치/등록 절차: docs/runbooks/hiring-daily-schedule.md
#
# 동작: 중복 기동 방지(flock) → 수집(레거시 분석 포함) && 신규 분석 → 실패 시 Discord 알림.
# 멱등: run_daily=ON CONFLICT, run_analyzers=dedupe(as_of) → 같은 날 재실행 안전.
set -uo pipefail

# ── 경로 해석 (cron의 임의 CWD에서도 안전) ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"   # services/agent-worker
REPO_ROOT="$(cd "$AGENT_DIR/../.." && pwd)"

# uv PATH 보장 — cron은 PATH가 /usr/bin:/bin 수준이라 'uv: command not found' 방지.
# uv 설치 위치가 다르면 런북 안내대로 이 줄을 조정.
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

# .env 자동 로드(export) — 셸(DISCORD_WEBHOOK_URL)·파이썬(DATABASE_URL/HIRING_DATALAB_*) 공용.
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
set -a
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
set +a

LOG_DIR="${HIRING_LOG_DIR:-/var/log/hiring}"
LOCK_FILE="${HIRING_LOCK:-/tmp/hiring-daily.lock}"
DATE="$(date +%F)"
mkdir -p "$LOG_DIR"

COLLECT_LOG="$LOG_DIR/$DATE.log"
ANALYZE_LOG="$LOG_DIR/$DATE.analyze.log"

# ── 중복 기동 방지 — 락 실패는 '정상 방어'이므로 조용히 exit 0 (알림/실패 신호 금지) ──
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 다른 인스턴스가 실행 중 — skip" >>"$COLLECT_LOG"
  exit 0
fi

# ── 일별 체인: 수집(Step1+레거시분석) && 신규 분석. 수집 실패 시 분석 skip. ──
cd "$AGENT_DIR"
rc=0
{
  echo "[$(date '+%F %T')] ▶ run_daily_hiring_pipeline 시작"
  uv run python script/run_daily_hiring_pipeline.py
} >>"$COLLECT_LOG" 2>&1 \
  && {
    echo "[$(date '+%F %T')] ▶ run_analyzers 시작"
    uv run python run_analyzers.py
  } >>"$ANALYZE_LOG" 2>&1 \
  || rc=$?

# ── 침묵 실패 방지: exit≠0이면 Discord 트랩 (DISCORD_WEBHOOK_URL 있을 때만) ──
# 역할 분담: ops_daemon은 collector_runs에 '기록된' 거부율/전건실패만 알림.
# 이 트랩은 run이 시작도 못 하거나(uv/DB/import 실패) 크래시로 row조차 없는 '진짜 침묵 실패' 커버.
if [ "$rc" -ne 0 ]; then
  msg="🚨 hiring 일별 파이프라인 실패 (exit=$rc · $(hostname) · $DATE) — 서버 로그 확인: $LOG_DIR/$DATE*.log"
  echo "[$(date '+%F %T')] $msg" >>"$COLLECT_LOG"
  if [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
    curl -fsS -X POST -H 'Content-Type: application/json' \
      -d "{\"content\": \"$msg\"}" "$DISCORD_WEBHOOK_URL" >/dev/null 2>&1 || true
  fi
  exit "$rc"
fi

echo "[$(date '+%F %T')] ✅ hiring 일별 파이프라인 완료" >>"$COLLECT_LOG"
