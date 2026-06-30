# Agent Worker Pipeline Schedule Runbook

Updated: 2026-06-25

This runbook describes how to run DART and Report collection, normalization, analysis, and final data-direction aggregation on a schedule.

Signal Alpha is not an investment recommendation service. The pipeline produces data direction, evidence, source agreement, and review flags. Do not describe these jobs as producing buy, sell, hold, target return, or timing recommendations.

## 1. Current Boundary

The scheduler must not contain collector or analyzer logic.

- `agent-worker` owns collection, normalization, analysis, queue handling, ML inference, aggregation, synthesis, and risk veto.
- The scheduler layer only calls internal `agent-worker` endpoints.
- `main-server` and `web` do not run collection or analysis jobs.
- PRICE collection remains the `agent-worker` lifespan daemon. PRICE analyzer reads DB data only.

Existing internal endpoints:

| Purpose | Endpoint |
|---|---|
| DART collection enqueue | `POST /internal/schedules/dart/collect` |
| Report collection enqueue | `POST /internal/schedules/report/collect` |
| Queue batch execution | `POST /internal/queue/{task_type}/run-batch` |

## 2. Recommended MVP Scheduling Model

Use an external scheduler first.

Recommended choices:

- Local development: manual PowerShell script or Windows Task Scheduler.
- Linux server: cron or systemd timer.
- Managed deployment: Cloud Scheduler, Railway Cron, GitHub Actions schedule, or another external scheduler that can reach the internal worker URL.

Avoid enabling a new `agent-worker` in-process scheduler daemon as the first step. If more than one worker instance is running, an in-process scheduler can enqueue duplicate work unless a distributed lock is added.

## 3. Collection Cadence

Suggested starting cadence:

| Source | Development | Operations Starting Point | Notes |
|---|---:|---:|---|
| DART | Every 30-60 minutes | Every 30-120 minutes, focused around market and disclosure hours | Respect OpenDART limits. Keep active stock count and document fetch settings bounded. |
| Report | 1-2 times per day | Every 6-24 hours | Report crawling and PDF parsing are heavier than DART. |
| Queue drain | Every 1-5 minutes | Every 1-5 minutes | Drain is cheap when queues are empty and lets source-specific jobs complete independently. |

Collection and analysis cadences should stay separate. Collection adds source work to `processing_queue`; queue drain workers decide how quickly normalization, analysis, and aggregation catch up.

## 4. Queue Drain Order

Run source-specific tasks first, then shared downstream tasks.

```text
collect_dart
collect_report
normalize_dart
process_report
normalize_report
analyze_dart
analyze_report
ml_infer
meta_combine
aggregate_signal
synthesize
risk_veto
```

Why this order:

- DART: `collect_dart -> normalize_dart -> analyze_dart`
- Report: `collect_report -> process_report -> normalize_report -> analyze_report`
- DART and Report analyzers enqueue `ml_infer`.
- ML may enqueue `meta_combine` or go directly to `aggregate_signal`.
- Aggregation writes `final_signals` and may enqueue `synthesize`.
- Synthesis may enqueue `risk_veto`.

Each queue type is idempotent through existing dedupe and upsert behavior. Still, keep `max_runs` bounded so one source cannot monopolize a scheduled window.

## 5. Local PowerShell Runner

The local runner calls only HTTP endpoints.

```powershell
cd services/agent-worker
.\ops\run_agent_pipeline_schedule.ps1 `
  -WorkerBaseUrl "http://localhost:8011" `
  -Mode All `
  -DartLimit 100 `
  -ReportLimit 100 `
  -ReportDaysBack 7 `
  -ReportMaxPages 20 `
  -MaxRuns 20
```

Modes:

| Mode | Behavior |
|---|---|
| `All` | Enqueue DART and Report collection, then drain queue tasks. |
| `Collect` | Only call schedule endpoints. |
| `Drain` | Only run the bounded fair queue cycle endpoint. |

Useful local variations:

```powershell
# Print planned calls without contacting the worker
.\ops\run_agent_pipeline_schedule.ps1 -Mode All -DryRun

# Check worker health first, then drain queues
.\ops\run_agent_pipeline_schedule.ps1 -Mode Drain -HealthCheck -MaxRuns 20

# Drain only, useful after manually enqueueing tasks
.\ops\run_agent_pipeline_schedule.ps1 -Mode Drain -MaxRuns 50

# DART only
.\ops\run_agent_pipeline_schedule.ps1 -Mode All -SkipReport

# Report only
.\ops\run_agent_pipeline_schedule.ps1 -Mode All -SkipDart -ReportDaysBack 14 -ReportMaxPages 50
```

### 5.1 Automatic publishing helper (Windows Task Scheduler)

`ops/register_report_autopublish_tasks.ps1` registers the runner as recurring
Windows scheduled tasks so the worker publishes signals without manual runs.

```powershell
cd services/agent-worker
# Register (CorpCodeSync weekly + DartCollectDrain 30m + ReportCollect 2x/day + QueueDrain 5m)
.\ops\register_report_autopublish_tasks.ps1 -WorkerBaseUrl "http://localhost:8011"

# Remove all of them (rollback)
.\ops\register_report_autopublish_tasks.ps1 -Remove
```

Notes:

- The worker must be running at `-WorkerBaseUrl`; tasks only call its HTTP endpoints.
- Publishing needs a scoring source. `REPORT`/`PRICE` are not scoring sources
  (`SCORING_SOURCES = {DART, HIRING, PATENT, DATALAB}` in `aggregation/tasks.py`),
  so at least one of DART/hiring/patent/datalab must produce a result for
  `final_signals.is_published` to become true.
- DART collection requires the `dart_corp_codes` mapping. The script runs an
  initial `POST /internal/dart/corp-codes/sync` on registration (skip with
  `-SkipInitialSync`) and re-syncs weekly via the `CorpCodeSync` task; without
  it DART collection fails with `corp_code is not mapped`.
- Tasks default to "run only when the user is logged on". Use
  `-RunWhenLoggedOff` (S4U) to keep them running after sign-out.

## 6. Local Smoke Test

Use this order before adding an OS scheduler.

1. Confirm the script plan without hitting the worker:

```powershell
cd services/agent-worker
.\ops\run_agent_pipeline_schedule.ps1 -Mode All -DryRun -MaxRuns 5
```

Expected:

- Output includes `DRY-RUN POST /internal/schedules/dart/collect`.
- Output includes `DRY-RUN POST /internal/schedules/report/collect`.
- Output includes queue calls from `collect_dart` through `risk_veto`.

2. Start local services:

```powershell
docker compose up -d postgres agent-worker
```

3. Run a worker health check:

```powershell
cd services/agent-worker
.\ops\run_agent_pipeline_schedule.ps1 -Mode Drain -HealthCheck -DryRun
.\ops\run_agent_pipeline_schedule.ps1 -Mode Drain -HealthCheck -MaxRuns 1
```

Expected:

- Dry-run prints `DRY-RUN GET /health`.
- Real run reaches `/health` and exits without a request error.
- Empty queues are valid and should show `run_count=0`.

4. Run a small collection pass:

```powershell
.\ops\run_agent_pipeline_schedule.ps1 `
  -Mode Collect `
  -DartLimit 5 `
  -ReportLimit 5 `
  -ReportDaysBack 3 `
  -ReportMaxPages 3
```

5. Run a bounded fair drain cycle:

```powershell
.\ops\run_agent_pipeline_schedule.ps1 -Mode Drain -MaxRuns 5
```

6. Inspect queue health:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue"
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/tasks?status=failed&limit=20"
```

If this smoke test is stable, increase limits and then add Windows Task Scheduler, cron, or a managed scheduler.

## 7. Docker Compose Manual Execution

Start local services:

```powershell
docker compose up -d postgres agent-worker
```

Apply migrations and seeds if needed:

```powershell
docker compose run --rm db-migrate apply --seeds
```

Confirm worker health:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/health"
```

Enqueue DART collection:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/dart/collect" `
  -ContentType "application/json" `
  -Body '{"limit":100,"priority":"batch"}'
```

Enqueue Report collection:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/collect" `
  -ContentType "application/json" `
  -Body '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'
```

Drain queues manually:

```powershell
$tasks = @(
  "collect_dart",
  "collect_report",
  "normalize_dart",
  "process_report",
  "normalize_report",
  "analyze_dart",
  "analyze_report",
  "ml_infer",
  "meta_combine",
  "aggregate_signal",
  "synthesize",
  "risk_veto"
)

foreach ($task in $tasks) {
  Invoke-RestMethod `
    -Method Post `
    -Uri "http://localhost:8011/internal/queue/$task/run-batch" `
    -ContentType "application/json" `
    -Body '{"max_runs":20}'
}
```

Check queue state:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue"
```

Check recent DART analysis:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/dart/analysis-results?stock_code=005930&limit=5"
```

## 8. Windows Task Scheduler

Use this for a local or classroom PC that may not run all day.

Program:

```text
powershell.exe
```

Arguments:

```text
-NoProfile -ExecutionPolicy Bypass -File C:\Users\804\workspace\final_project\services\agent-worker\ops\run_agent_pipeline_schedule.ps1 -WorkerBaseUrl http://localhost:8011 -Mode All -MaxRuns 20
```

Start in:

```text
C:\Users\804\workspace\final_project\services\agent-worker
```

Recommended triggers:

- DART collection and drain: every 30-60 minutes while the PC is expected to be on.
- Report collection and drain: once or twice per day.
- Drain-only task: every 5 minutes if collection is scheduled separately.

For separate collection and drain tasks:

```text
-File ...\run_agent_pipeline_schedule.ps1 -Mode Collect
-File ...\run_agent_pipeline_schedule.ps1 -Mode Drain -MaxRuns 50
```

## 9. Linux Cron Example

Use internal network URLs. Do not expose `/internal/*` endpoints publicly without an authentication or network control layer.

For Linux hosts without PowerShell, use `curl` in the same order:

```bash
WORKER_INTERNAL_URL="${WORKER_INTERNAL_URL:-http://127.0.0.1:8011}"

curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/schedules/dart/collect" \
  -H 'Content-Type: application/json' \
  -d '{"limit":100,"priority":"batch"}'

curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/schedules/report/collect" \
  -H 'Content-Type: application/json' \
  -d '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'

for task in \
  collect_dart collect_report normalize_dart process_report normalize_report \
  analyze_dart analyze_report ml_infer meta_combine aggregate_signal synthesize risk_veto
do
  curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/queue/$task/run-batch" \
    -H 'Content-Type: application/json' \
    -d '{"max_runs":20}'
done
```

Cron sketch:

```cron
# Every 5 minutes: drain queues.
*/5 * * * * WORKER_INTERNAL_URL=http://127.0.0.1:8011 /opt/signal-alpha/services/agent-worker/ops/run-agent-pipeline-drain.sh

# Every hour during the day: enqueue DART collection.
0 8-20 * * * curl -fsS -X POST http://127.0.0.1:8011/internal/schedules/dart/collect -H 'Content-Type: application/json' -d '{"limit":100,"priority":"batch"}'

# Twice daily: enqueue Report collection.
15 8,17 * * * curl -fsS -X POST http://127.0.0.1:8011/internal/schedules/report/collect -H 'Content-Type: application/json' -d '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'
```

## 10. Managed Scheduler Example

For Cloud Scheduler, Railway Cron, GitHub Actions, or another managed scheduler:

1. Keep `agent-worker` running.
2. Point the scheduler to an internal or protected worker URL.
3. Trigger collection endpoints on a source-specific cadence.
4. Trigger queue drain every 1-5 minutes.
5. Monitor `/internal/stats/queue`, failed tasks, and dead letters.

Do not call `agent-worker` internal endpoints from a public GitHub Actions runner unless the URL is protected by VPN, private networking, a token-checking proxy, or equivalent access control.

## 11. Failure Handling

Expected behavior:

- Empty queues return `run_count=0`.
- Individual task failures stop the current task type batch.
- Retryable failures remain in `processing_queue`.
- Terminal failures are archived through the dead-letter path.

Operational checks:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue"
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/tasks?status=failed&limit=20"
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/dead-letter?replayed=false"
```

If failures are caused by missing API keys, fix environment variables and restart `agent-worker`.

## 12. Initial Rollout Recommendation

Start with this order:

1. Run the PowerShell script manually in `Drain` mode and confirm empty queues are harmless.
2. Run `Collect` mode with a small limit.
3. Run `Drain` mode with `MaxRuns=5`.
4. Increase `MaxRuns` after observing task duration and API usage.
5. Add OS or managed scheduler only after manual runs are stable.
