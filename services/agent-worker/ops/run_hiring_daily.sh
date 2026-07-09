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

# 로그를 파일과 stdout 양쪽에 쓴다.
# 컨테이너(k8s CronJob)에서는 stdout 이 유일한 로그 채널이다 — 파일은 파드와 함께 사라져
# `kubectl logs` 가 빈 출력을 준다. 그러면 실패해도(exit 1) 원인을 볼 방법이 없다.
# HIRING_LOG_STDOUT=0 으로 끄면 기존처럼 파일에만 쓴다(로컬 cron 용).
# 주의: 파이프로 바뀌므로 exit code 는 pipefail(위 set -uo pipefail)이 보존한다.
tee_log() {
  if [ "${HIRING_LOG_STDOUT:-1}" = "1" ]; then tee -a "$1"; else cat >>"$1"; fi
}

# Discord 알림 헬퍼 (DISCORD_WEBHOOK_URL 있을 때만, 실패해도 래퍼는 안 죽음).
notify() {
  echo "[$(date '+%F %T')] $1" | tee_log "$COLLECT_LOG"
  [ -n "${DISCORD_WEBHOOK_URL:-}" ] || return 0
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"content\": \"$1\"}" "$DISCORD_WEBHOOK_URL" >/dev/null 2>&1 || true
}

# ── 중복 기동 방지 — 락 실패는 '정상 방어'이므로 조용히 exit 0 (알림/실패 신호 금지) ──
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] 다른 인스턴스가 실행 중 — skip" | tee_log "$COLLECT_LOG"
  exit 0
fi

cd "$AGENT_DIR"

# ── catch-up: 오늘 이미 성공 적재됐으면 skip (자주/놓친-후 트리거해도 그날 1회만 실제 실행) ──
# exit 0 = 오늘 완료 → skip. exit 1(미완료)/2(DB 판단불가) → 진행(실행을 막지 않음).
if uv run python ops/check_hiring_freshness.py --quiet 2>&1 | tee_log "$COLLECT_LOG"; then
  echo "[$(date '+%F %T')] 오늘 이미 적재 완료 — skip" | tee_log "$COLLECT_LOG"
  exit 0
fi

# ── 일별 체인: 수집(Step1+레거시분석) && 신규 분석. 수집 실패 시 분석 skip. ──
# PIPESTATUS[0] 로 파이프 왼쪽(=uv run)의 exit code 를 읽는다 — tee 의 0 에 가려지지 않게.
rc=0
{
  echo "[$(date '+%F %T')] ▶ run_daily_hiring_pipeline 시작"
  uv run python script/run_daily_hiring_pipeline.py
} 2>&1 | tee_log "$COLLECT_LOG"
rc=${PIPESTATUS[0]}

if [ "$rc" -eq 0 ]; then
  {
    echo "[$(date '+%F %T')] ▶ run_analyzers 시작"
    uv run python run_analyzers.py
  } 2>&1 | tee_log "$ANALYZE_LOG"
  rc=${PIPESTATUS[0]}
fi

# ── 침묵 실패 방지: exit≠0이면 Discord 트랩 (DISCORD_WEBHOOK_URL 있을 때만) ──
# 역할 분담: ops_daemon은 collector_runs에 '기록된' 거부율/전건실패만 알림.
# 이 트랩은 run이 시작도 못 하거나(uv/DB/import 실패) 크래시로 row조차 없는 '진짜 침묵 실패' 커버.
if [ "$rc" -ne 0 ]; then
  notify "🚨 hiring 일별 파이프라인 실패 (exit=$rc · $(hostname) · $DATE) — 로그 확인: $LOG_DIR/$DATE*.log"
  exit "$rc"
fi

# ── 적재 확인: 체인이 exit 0이어도 오늘자 성공 run이 기록 안 됐으면(침묵 no-op) 알림 ──
if ! uv run python ops/check_hiring_freshness.py --quiet 2>&1 | tee_log "$COLLECT_LOG"; then
  notify "⚠️ hiring 파이프라인이 끝났지만 오늘($DATE)자 적재가 확인 안 됨 ($(hostname)) — 로그 확인"
fi

echo "[$(date '+%F %T')] ✅ hiring 일별 파이프라인 완료" | tee_log "$COLLECT_LOG"
