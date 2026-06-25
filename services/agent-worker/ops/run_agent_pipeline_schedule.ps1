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

  [int]$DartLimit = 100,
  [int]$ReportLimit = 100,
  [int]$ReportDaysBack = 7,
  [string]$ReportDateStart = "",
  [string]$ReportDateEnd = "",
  [int]$ReportMaxPages = 20,
  [int]$MaxRuns = 20,
  [int]$TimeoutSec = 120,

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
    [Parameter(Mandatory = $true)][hashtable]$Body
  )

  $base = $WorkerBaseUrl.TrimEnd("/")
  $uri = "$base$Path"
  $json = ConvertTo-JsonBody $Body
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
  $taskTypes = @()

  if (-not $SkipDart) {
    $taskTypes += @("collect_dart")
  }
  if (-not $SkipReport) {
    $taskTypes += @("collect_report")
  }
  if (-not $SkipDart) {
    $taskTypes += @("normalize_dart")
  }
  if (-not $SkipReport) {
    $taskTypes += @("process_report", "normalize_report")
  }
  if (-not $SkipDart) {
    $taskTypes += @("analyze_dart")
  }
  if (-not $SkipReport) {
    $taskTypes += @("analyze_report")
  }

  $taskTypes += @(
    "ml_infer",
    "meta_combine",
    "aggregate_signal",
    "synthesize",
    "risk_veto"
  )

  foreach ($taskType in $taskTypes) {
    $result = Invoke-WorkerPost `
      -Path "/internal/queue/$taskType/run-batch" `
      -Body @{ max_runs = $MaxRuns }

    if ($null -ne $result -and $result.PSObject.Properties.Name -contains "run_count") {
      Write-Step "$taskType run_count=$($result.run_count)"
    }
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
