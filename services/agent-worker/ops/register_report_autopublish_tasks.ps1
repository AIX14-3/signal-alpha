<#
.SYNOPSIS
  리포트 자동발행용 Windows 예약 작업 4개를 등록/삭제한다.

.DESCRIPTION
  PR #506 러너(run_agent_pipeline_schedule.ps1)를 Windows Task Scheduler가 주기적으로
  호출하게 만들어, agent-worker가 스스로 수집 -> drain -> 집계 -> 발행(is_published=TRUE)
  하도록 한다. 러너는 워커의 내부 HTTP 엔드포인트만 호출하므로, 사전에 워커가
  -WorkerBaseUrl(기본 http://localhost:8011) 에서 떠 있어야 한다.

  등록되는 작업(SignalAlpha\ 폴더):
    - SignalAlpha\CorpCodeSync     : DART corp_code 동기화, 매주 월 07:50 (DART 수집 선행조건)
    - SignalAlpha\DartCollectDrain : DART 수집 + 큐 drain, 30분마다
    - SignalAlpha\ReportCollect    : 리포트 수집, 매일 08:15 / 17:15
    - SignalAlpha\QueueDrain       : 큐 drain 전용(집계/발행 진행), 5분마다

  발행 조건: REPORT 는 점수 소스가 아니다(SCORING_SOURCES = DART/ALTERNATIVE).
  DART 수집이 같이 돌아야 final_signals.is_published 가 TRUE 가 된다.
  => DartCollectDrain 작업을 비활성화하면 리포트만으론 발행되지 않는다.

  등록 시 corp_code 매핑을 즉시 1회 동기화한다(-SkipInitialSync 로 생략 가능).
  이를 건너뛰면 다음 주 월요일 CorpCodeSync 전까지 DART 수집이 실패해 발행이 0건이 된다.

.PARAMETER WorkerBaseUrl
  러너가 호출할 워커 내부 URL. 기본 http://localhost:8011.

.PARAMETER SkipInitialSync
  지정 시 등록 직후의 corp_code 1회 동기화를 생략한다(이미 동기화된 환경 재등록용).

.PARAMETER Remove
  지정 시 위 4개 작업을 삭제만 하고 종료한다.

.EXAMPLE
  # 등록
  powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\register_report_autopublish_tasks.ps1

.EXAMPLE
  # 삭제(롤백)
  powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\register_report_autopublish_tasks.ps1 -Remove
#>
#Requires -Version 5.1

[CmdletBinding()]
param(
  [string]$WorkerBaseUrl = "http://localhost:8011",
  [int]$MaxRuns = 50,
  [switch]$SkipInitialSync,
  [switch]$RunWhenLoggedOff,
  [switch]$Remove
)

$ErrorActionPreference = "Stop"

$folder = "SignalAlpha"
$names = @(
  "$folder\CorpCodeSync",
  "$folder\DartCollectDrain",
  "$folder\ReportCollect",
  "$folder\QueueDrain"
)

function Remove-Existing {
  foreach ($name in $names) {
    $existing = Get-ScheduledTask -TaskName (Split-Path $name -Leaf) `
      -TaskPath "\$folder\" -ErrorAction SilentlyContinue
    if ($existing) {
      Unregister-ScheduledTask -TaskName (Split-Path $name -Leaf) `
        -TaskPath "\$folder\" -Confirm:$false
      Write-Host "removed: $name"
    }
  }
}

if ($Remove) {
  Remove-Existing
  Write-Host "done (removed)."
  return
}

# 러너 절대 경로 — 이 스크립트와 같은 ops 디렉터리에 있다.
$runner = Join-Path $PSScriptRoot "run_agent_pipeline_schedule.ps1"
if (-not (Test-Path $runner)) {
  throw "Runner not found: $runner (PR #506 의 run_agent_pipeline_schedule.ps1 를 먼저 배치하세요)"
}
$workDir = Split-Path $PSScriptRoot   # services/agent-worker

# 기존 동일 작업 정리(멱등 재등록)
Remove-Existing

# 겹치는 실행 방지 + PC 가용 시 실행 + 배터리에서도 실행
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries

# 기본은 "로그온 시에만 실행"(비관리자에서 안전). -RunWhenLoggedOff 지정 시 S4U 로
# 로그오프 상태에서도 실행한다(자격증명 저장 불필요). 각 Register 호출에 splat 한다.
$regExtra = @{}
if ($RunWhenLoggedOff) {
  $regExtra["Principal"] = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
}

function New-RunnerAction {
  param([string]$ExtraArgs)
  $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" " +
             "-WorkerBaseUrl `"$WorkerBaseUrl`" $ExtraArgs"
  return New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument $argLine -WorkingDirectory $workDir
}

# (D) DART corp_code 동기화 — 매주 월 07:50 (DART 수집의 선행 조건).
#     corp_code 매핑이 없으면 DART 수집이 "corp_code is not mapped" 로 실패하고,
#     그러면 점수 소스가 없어 리포트가 발행되지 않는다. 신규 상장 반영 위해 주 1회.
$syncArg = "-NoProfile -ExecutionPolicy Bypass -Command " +
  "`"Invoke-RestMethod -Method Post -Uri '$($WorkerBaseUrl.TrimEnd('/'))/internal/dart/corp-codes/sync' " +
  "-ContentType 'application/json' -Body '{}' -TimeoutSec 180`""
$actD = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $syncArg -WorkingDirectory $workDir
$trigD = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "07:50"
Register-ScheduledTask -TaskName "CorpCodeSync" -TaskPath "\$folder\" `
  -Action $actD -Trigger $trigD -Settings $settings @regExtra `
  -Description "Signal Alpha: DART corp_code 동기화 (주1회, DART 수집 선행조건)" -Force | Out-Null
Write-Host "registered: $folder\CorpCodeSync (weekly Mon 07:50)"

# 반복 트리거의 무기한 지속 — 일부 Windows 빌드는 RepetitionDuration 미지정 시 반복을
# 무기한으로 두지 않으므로 큰 유한값으로 명시한다([TimeSpan]::MaxValue 거부 빌드 회피).
$forever = New-TimeSpan -Days 3650

# (A) DART 수집 + drain — 30분마다(무기한 반복)
$trigA = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration $forever
$actA = New-RunnerAction -ExtraArgs "-Mode All -SkipReport -DartLimit 100 -MaxRuns 30"
Register-ScheduledTask -TaskName "DartCollectDrain" -TaskPath "\$folder\" `
  -Action $actA -Trigger $trigA -Settings $settings @regExtra `
  -Description "Signal Alpha: DART 수집 + 큐 drain (30분)" -Force | Out-Null
Write-Host "registered: $folder\DartCollectDrain (every 30 min)"

# (B) 리포트 수집 — 매일 08:15, 17:15
$trigB = @(
  (New-ScheduledTaskTrigger -Daily -At "08:15"),
  (New-ScheduledTaskTrigger -Daily -At "17:15")
)
$actB = New-RunnerAction -ExtraArgs "-Mode Collect -SkipDart -ReportLimit 100 -ReportDaysBack 7 -ReportMaxPages 20"
Register-ScheduledTask -TaskName "ReportCollect" -TaskPath "\$folder\" `
  -Action $actB -Trigger $trigB -Settings $settings @regExtra `
  -Description "Signal Alpha: 리포트 수집 (매일 2회)" -Force | Out-Null
Write-Host "registered: $folder\ReportCollect (daily 08:15, 17:15)"

# (C) 큐 drain 전용 — 5분마다(집계/발행 진행)
$trigC = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration $forever
$actC = New-RunnerAction -ExtraArgs "-Mode Drain -MaxRuns $MaxRuns"
Register-ScheduledTask -TaskName "QueueDrain" -TaskPath "\$folder\" `
  -Action $actC -Trigger $trigC -Settings $settings @regExtra `
  -Description "Signal Alpha: 큐 drain (5분)" -Force | Out-Null
Write-Host "registered: $folder\QueueDrain (every 5 min)"

# 초기 corp_code 1회 동기화 — 주간 CorpCodeSync 는 다음 월요일에야 돌므로, 지금 매핑이
# 없으면 그때까지 DART 수집이 실패해 발행이 0건이 된다. 워커가 떠 있어야 성공한다.
if (-not $SkipInitialSync) {
  $syncUri = "$($WorkerBaseUrl.TrimEnd('/'))/internal/dart/corp-codes/sync"
  Write-Host ""
  Write-Host "initial corp_code sync: POST $syncUri"
  try {
    $r = Invoke-RestMethod -Method Post -Uri $syncUri `
      -ContentType "application/json" -Body "{}" -TimeoutSec 180
    Write-Host ("  ok: upserted_count={0} listed_count={1}" -f $r.upserted_count, $r.listed_count)
  }
  catch {
    Write-Warning ("initial corp_code sync 실패: {0}" -f $_.Exception.Message)
    Write-Warning "워커가 $WorkerBaseUrl 에서 떠 있는지 확인 후, 아래를 수동 실행하세요:"
    Write-Warning "  Invoke-RestMethod -Method Post -Uri '$syncUri' -ContentType 'application/json' -Body '{}'"
  }
}

Write-Host ""
Write-Host "done. 확인:  Get-ScheduledTask -TaskPath '\$folder\'"
Write-Host "롤백:  .\ops\register_report_autopublish_tasks.ps1 -Remove"
Write-Host "주의: 워커가 $WorkerBaseUrl 에서 떠 있어야 작업이 실제로 동작합니다."
