<#
일별 채용 수집·분석 자동 실행 래퍼 (#297) — Windows 작업 스케줄러용.
catch-up(오늘 이미 적재됐으면 skip) + 실패/적재미확인 시 Discord 알림. 멱등이라 놓친-후/중복 트리거 안전.
등록 절차(마우스 클릭 가이드): docs/runbooks/hiring-daily-schedule.md
#>
#Requires -Version 5.1
$ErrorActionPreference = 'Continue'

# ── 경로 자가 해석 (작업 스케줄러 'Start in' 미설정에도 동작) ──
$ScriptDir = $PSScriptRoot
$AgentDir  = Split-Path $ScriptDir -Parent
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir '..\..\..')).Path

# uv PATH 보장 (스케줄러 세션 PATH 누락 대비; uv 설치 위치 다르면 조정)
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"

# ── 로그 ──
$LogDir = if ($env:HIRING_LOG_DIR) { $env:HIRING_LOG_DIR } else { Join-Path $RepoRoot 'logs\hiring' }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Date = Get-Date -Format 'yyyy-MM-dd'
$Log  = Join-Path $LogDir "$Date.log"
function Log([string]$m) { "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m | Add-Content -Path $Log -Encoding utf8 }

# ── .env 로드 (DISCORD_WEBHOOK_URL / DATABASE_URL / HIRING_DATALAB_*) ──
$EnvFile = if ($env:ENV_FILE) { $env:ENV_FILE } else { Join-Path $RepoRoot '.env' }
if (Test-Path $EnvFile) {
  foreach ($line in Get-Content $EnvFile) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $i = $t.IndexOf('=')
    $k = $t.Substring(0, $i).Trim()
    $v = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($k, $v, 'Process')
  }
}

function Notify([string]$msg) {
  Log $msg
  if ($env:DISCORD_WEBHOOK_URL) {
    try {
      Invoke-RestMethod -Method Post -Uri $env:DISCORD_WEBHOOK_URL -ContentType 'application/json' `
        -Body (@{ content = $msg } | ConvertTo-Json) -TimeoutSec 10 | Out-Null
    } catch { }
  }
}

# ── 단일 인스턴스(중복 기동 방지) — 락 실패는 정상 방어 → 조용히 종료 ──
$mutex = New-Object System.Threading.Mutex($false, 'Local\hiring-daily-lock')
if (-not $mutex.WaitOne(0)) { Log '다른 인스턴스 실행 중 — skip'; exit 0 }

try {
  Set-Location $AgentDir

  # catch-up: 오늘 이미 성공 적재됐으면 skip (exit 0). 1(미완료)/2(DB판단불가)면 진행.
  & uv run python ops/check_hiring_freshness.py --quiet | Out-Null
  if ($LASTEXITCODE -eq 0) { Log '오늘 이미 적재 완료 — skip'; exit 0 }

  # 체인: 수집(레거시 분석 포함) → 성공 시 신규 분석
  Log '▶ run_daily_hiring_pipeline 시작'
  & uv run python script/run_daily_hiring_pipeline.py
  $rc = $LASTEXITCODE
  if ($rc -eq 0) {
    Log '▶ run_analyzers 시작'
    & uv run python run_analyzers.py
    $rc = $LASTEXITCODE
  }

  if ($rc -ne 0) {
    Notify "🚨 hiring 일별 파이프라인 실패 (exit=$rc · $env:COMPUTERNAME · $Date) — 로그: $Log"
    exit $rc
  }

  # 적재 확인: exit 0이어도 오늘자 성공 run이 없으면(침묵 no-op) 알림.
  & uv run python ops/check_hiring_freshness.py --quiet | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Notify "⚠️ hiring 파이프라인이 끝났지만 오늘($Date)자 적재가 확인 안 됨 ($env:COMPUTERNAME) — 로그: $Log"
  }
  Log 'OK hiring 일별 파이프라인 완료'
}
finally {
  $mutex.ReleaseMutex(); $mutex.Dispose()
}
