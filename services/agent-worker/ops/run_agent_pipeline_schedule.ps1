<#
Runs the Signal Alpha agent-worker collection and queue-drain schedule through
internal HTTP endpoints.

This script does not contain collector or analyzer logic. It only calls:
- /internal/schedules/dart/collect
- /internal/schedules/report/collect
- /internal/queue/{task_type}/run-batch
#>
#Requires -Version 5.1

[CmdletBinding()]
param(
  [string]$WorkerBaseUrl = $(if ($env:AGENT_WORKER_INTERNAL_URL) { $env:AGENT_WORKER_INTERNAL_URL } else { "http://localhost:8011" }),

  [ValidateSet("All", "Collect", "Drain")]
  [string]$Mode = "All",

  [int]$DartLimit = 10,
  [int]$ReportLimit = 100,
  [int]$ReportDaysBack = 7,
  [string]$ReportDateStart = "",
  [string]$ReportDateEnd = "",
  [int]$ReportMaxPages = 20,
  [int]$MaxRuns = 20,
  [int]$TimeoutSec = 120,
  [int]$DrainTimeoutSec = 600,

  [switch]$SkipDart,
  [switch]$SkipReport,
  [switch]$ContinueOnError,
  [switch]$DryRun,
  [switch]$HealthCheck
)

$ErrorActionPreference = "Stop"

function Write-Step {
  param([string]$Message)
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$stamp] $Message"
}

function ConvertTo-JsonBody {
  param([hashtable]$Body)
  return ($Body | ConvertTo-Json -Depth 10 -Compress)
}

function Invoke-WorkerPost {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][hashtable]$Body,
    [int]$TimeoutSecOverride = 0
  )

  $base = $WorkerBaseUrl.TrimEnd("/")
  $uri = "$base$Path"
  $json = ConvertTo-JsonBody $Body
  $effectiveTimeout = if ($TimeoutSecOverride -gt 0) { $TimeoutSecOverride } else { $TimeoutSec }
  if ($DryRun) {
    Write-Step "DRY-RUN POST $Path $json"
    return [pscustomobject]@{
      status = "dry_run"
      path = $Path
      body = $Body
      run_count = 0
    }
  }

  Write-Step "POST $Path $json"

  try {
    return Invoke-RestMethod `
      -Method Post `
      -Uri $uri `
      -ContentType "application/json" `
      -Body $json `
      -TimeoutSec $effectiveTimeout
  }
  catch {
    $message = "Request failed: $Path - $($_.Exception.Message)"
    if ($ContinueOnError) {
      Write-Warning $message
      return @{ status = "failed"; error = $message }
    }
    throw $message
  }
}

function Invoke-WorkerGet {
  param(
    [Parameter(Mandatory = $true)][string]$Path
  )

  $base = $WorkerBaseUrl.TrimEnd("/")
  $uri = "$base$Path"

  if ($DryRun) {
    Write-Step "DRY-RUN GET $Path"
    return [pscustomobject]@{
      status = "dry_run"
      path = $Path
    }
  }

  Write-Step "GET $Path"
  try {
    return Invoke-RestMethod `
      -Method Get `
      -Uri $uri `
      -TimeoutSec $TimeoutSec
  }
  catch {
    $message = "Request failed: $Path - $($_.Exception.Message)"
    if ($ContinueOnError) {
      Write-Warning $message
      return @{ status = "failed"; error = $message }
    }
    throw $message
  }
}

function Invoke-CollectionSchedules {
  if (-not $SkipDart) {
    $dartBody = @{
      limit = $DartLimit
      priority = "batch"
    }
    [void](Invoke-WorkerPost -Path "/internal/schedules/dart/collect" -Body $dartBody)
  }

  if (-not $SkipReport) {
    $reportBody = @{
      limit = $ReportLimit
      days_back = $ReportDaysBack
      max_pages = $ReportMaxPages
      priority = "batch"
    }
    if ($ReportDateStart) {
      $reportBody["date_start"] = $ReportDateStart
    }
    if ($ReportDateEnd) {
      $reportBody["date_end"] = $ReportDateEnd
    }
    [void](Invoke-WorkerPost -Path "/internal/schedules/report/collect" -Body $reportBody)
  }
}

function Invoke-QueueDrain {
  # 공정 라운드로빈 drain — 서버측 run-cycle 이 task_type 간 한 패스당 1개씩 순환 처리해
  # 어떤 한 type(특히 collect_dart)도 다른 type 을 굶기지 못한다. 과거의 "task_type 을
  # 고정 순서로 max_runs 까지 연속 처리(=큐가 빌 때까지)" 드레인은 제거됨.
  # 캡 계획은 서버 DEFAULT_CYCLE_PLAN(수집기 특성별 차등) 사용. Skip* 플래그는 수집(인큐)
  # 단계에만 영향 — 스킵된 type 은 인큐된 작업이 없어 자연히 idle 처리된다.
  # 빈 본문 호환을 위해 max_passes 만 명시한다(서버 기본 plan 사용).
  $result = Invoke-WorkerPost `
    -Path "/internal/queue/run-cycle" `
    -Body @{ max_passes = 10000 } `
    -TimeoutSecOverride $DrainTimeoutSec

  if ($null -ne $result -and $result.PSObject.Properties.Name -contains "total_runs") {
    Write-Step "run-cycle passes=$($result.passes) total_runs=$($result.total_runs)"
  }
}

Write-Step "agent pipeline schedule start mode=$Mode base=$WorkerBaseUrl"

if ($HealthCheck) {
  [void](Invoke-WorkerGet -Path "/health")
}

if ($Mode -eq "All" -or $Mode -eq "Collect") {
  Invoke-CollectionSchedules
}

if ($Mode -eq "All" -or $Mode -eq "Drain") {
  Invoke-QueueDrain
}

Write-Step "agent pipeline schedule complete"
