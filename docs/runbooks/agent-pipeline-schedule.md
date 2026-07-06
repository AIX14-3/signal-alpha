# Agent Worker Pipeline Schedule Runbook

Updated: 2026-07-01

This runbook describes how to run Price, DART, Report, and alternative-data collection triggers, queue draining, analysis, and final data-direction aggregation on a schedule.

Signal Alpha is not an investment recommendation service. The pipeline produces data direction, evidence, source agreement, and review flags. Do not describe these jobs as producing buy, sell, hold, target return, or timing recommendations.

## 1. Current Boundary

The scheduler must not contain collector or analyzer logic.

- `agent-worker` owns collection, normalization, analysis, queue handling, ML inference, aggregation, synthesis, and risk veto.
- The scheduler layer only calls internal `agent-worker` endpoints or existing worker CLI entrypoints; it must not implement collection or analysis logic.
- Internal HTTP calls must send `X-Internal-Token`.
- `main-server` and `web` do not run collection or analysis jobs.
- PRICE collection remains the `agent-worker` lifespan daemon. PRICE analyzer reads DB data only.
- Hiring collection remains the dedicated hiring crawler CronJob because it needs the browser-enabled crawler image.

Existing internal endpoints:

| Purpose | Endpoint |
|---|---|
| Price collection trigger | `POST /internal/price/collect` |
| DART collection enqueue | `POST /internal/schedules/dart/collect` |
| Report collection enqueue | `POST /internal/schedules/report/collect` |
| Alternative target | local collector/analyzer CLIs |
| Queue cycle execution | `POST /internal/queue/run-cycle` |

## 2. Recommended MVP Scheduling Model

Use the DB-backed scheduler agent for managed operations.

The scheduler agent is `services/agent-worker/run_scheduler_instance.py`. It polls the backend-owned `collection_schedules` table, evaluates `enabled`, `run_at_local`, `frequency_minutes`, `active_from_local`, `active_until_local`, `timezone`, `targets`, and `manual_trigger_requested_at`, then triggers the selected worker entrypoints. It records `last_run_at`, `last_status`, `last_detail`, and `next_run_at` back to each fired row.

The default backend seed uses source-specific rows so each source can keep a separate cadence:

- `price-collection`
- `dart-collection`
- `report-collection`
- `alternative-collection`

The legacy `daily-collection` row is disabled by the split-source migration to avoid duplicate PRICE/DART firing.

Cadence fields:

- `run_at_local`: daily anchor time and default active-window start.
- `frequency_minutes`: repeat interval in minutes. `1440` keeps daily behavior.
- `active_from_local`: optional local time when repeat firing can start.
- `active_until_local`: optional local time when repeat firing stops for the day.

The scheduler fires a repeat row when it is inside the active window and `last_run_at + frequency_minutes` has elapsed. Manual triggers still bypass cadence checks and fire on the next polling cycle.

The `alternative` target runs Patent/DataLab collection through `run_collectors.py` and Hiring/Patent/DataLab analysis through `run_analyzers.py`. Hiring collection remains the dedicated hiring crawler CronJob.

Recommended choices:

- Local development: manual PowerShell script for smoke tests, or `uv run python run_scheduler_instance.py --once` for one DB-backed evaluation.
- Managed deployment: one scheduler deployment running `python run_scheduler_instance.py`, as shown in `deploy/k8s/scheduler.yaml`.
- Emergency/manual operations: admin schedule "trigger" updates `manual_trigger_requested_at`; the scheduler agent fires it on the next polling cycle.

The DB-backed scheduler uses a PostgreSQL advisory lock before firing due or manual schedules. If another scheduler instance already holds the lock, the current cycle returns `lock-held` and does not enqueue duplicate collection work.

Every fired run also writes a row to `collection_schedule_runs`, then updates `collection_schedules.last_*` for the current summary. Operators can inspect recent history through `GET /api/admin/schedules/{schedule_id}/runs`.

Each fired run stores scheduler-agent decision metadata in the JSON detail:
`decision` records the scheduler policy, action, trigger reason, schedule identity, and selected targets; `targets` records the per-target trigger result. This keeps the scheduler explainable without moving collection or analysis logic into the scheduler layer.

Before scheduled runs fire, the scheduler agent reads `/internal/stats/queue` and applies a conservative backpressure policy. If pending plus retrying work exceeds `SCHEDULER_BACKPRESSURE_MAX_WAITING`, the scheduled fire is skipped with `queue-backlog`. If failed work exceeds `SCHEDULER_BACKPRESSURE_MAX_FAILED`, the scheduled fire is skipped with `recent-failures`. Manual triggers bypass backpressure so operators can still force a controlled run.

Policy skips that replace an otherwise due scheduled run are also written to `collection_schedule_runs` with `status=skipped`. This records why the scheduler agent intentionally held back work without changing `collection_schedules.last_run_at` or advancing the source cadence.

Each schedule row can override execution policy without changing code:

- Report collection: `report_limit`, `report_days_back`, `report_max_pages`
- Alternative data: `alternative_collect_enabled`, `alternative_analyze_enabled`, `alternative_collect_timeout_seconds`, `alternative_analyze_timeout_seconds`
- Backpressure: `backpressure_max_waiting`, `backpressure_max_failed`

Admin exposes these policy fields and a dry-run action. Dry-run sends the schedule config to the worker's `/internal/schedules/dry-run` endpoint, reads current queue stats, and returns the scheduler-agent decision (`fire` or `skip`), reason, next planned run, and backpressure summary without enqueueing collection or analysis work.

Keep exactly one scheduler agent active for the schedule table. The advisory lock prevents duplicate firing during overlap, but one active scheduler remains the simplest operating model.

## 3. Collection Cadence

Suggested starting cadence:

| Source | Development | Operations Starting Point | Notes |
|---|---:|---:|---|
| DART | Every 30-60 minutes | Every 30-120 minutes, focused around market and disclosure hours | Respect OpenDART limits. Keep active stock count and document fetch settings bounded. |
| Report | 1-2 times per day | Every 6-24 hours | Report crawling and PDF parsing are heavier than DART. |
| Alternative | 1-2 times per day | Every 6-24 hours | Scheduler `alternative` handles Patent/DataLab collection and Hiring/Patent/DataLab analysis. Hiring collection stays on the browser-enabled CronJob. |
| Queue drain | Every 1-5 minutes | Every 1-5 minutes | Drain is cheap when queues are empty and lets source-specific jobs complete independently. |

Collection and analysis cadences should stay separate. Collection adds source work to `processing_queue`; queue drain workers decide how quickly normalization, analysis, and aggregation catch up.

Default source-specific scheduler rows:

| Row | `frequency_minutes` | Active Window (KST) | Targets |
|---|---:|---:|---|
| `price-collection` | 60 | 09:05-15:30 | `price` |
| `dart-collection` | 60 | 08:30-20:30 | `dart` |
| `report-collection` | 720 | 06:00-18:00 | `report` |
| `alternative-collection` | 720 | 05:30-17:30 | `alternative` |

## 4. Queue Drain Order

The scheduler agent only enqueues or triggers collection. Queue consumption is owned by the worker queue drain daemon (`QUEUE_DRAIN_DAEMON_ENABLED`) and by the same bounded fair cycle exposed at `POST /internal/queue/run-cycle`.

The current default fair-cycle plan is:

```text
collect_dart
collect_dart_ownership
collect_report
normalize_dart_ownership
normalize_dart
analyze_dart
process_report
normalize_report
analyze_report
embed_report
NORMALIZE_HIRING
NORMALIZE_PATENT
NORMALIZE_DATALAB
ENRICH_PATENT
ENRICH_HIRING
ANALYZE_DATALAB
ANALYZE_HIRING
ANALYZE_PATENT
analyze_price
src_infer
return_combine
requery_source
aggregate_signal
synthesize
publish_signals
record_episode_outcomes
```

Why this order:

- DART: `collect_dart -> normalize_dart -> analyze_dart`; ownership events use `collect_dart_ownership -> normalize_dart_ownership -> analyze_dart`.
- Report: `collect_report -> process_report -> normalize_report -> analyze_report`
- PRICE: scheduler triggers collection; `analyze_price` reads DB data only.
- Alternative data: external collection jobs feed normalize, enrich, and per-source analyze tasks.
- Downstream tasks infer source returns, combine return evidence, optionally re-query conflicted sources, aggregate source results, synthesize user-facing data-direction narratives, publish outputs, and record mature episode outcomes.

Each queue type is idempotent through existing dedupe and upsert behavior. The fair-cycle runner processes at most one task per type per pass, so one source cannot monopolize a scheduled window.

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
- DART currently publishes as evidence/coverage only: its agent returns
  `data_status="no_signal"`, so it appears in `score_breakdown.DART` and
  `SYNTHESIZE` but is excluded from the numeric `final_score` average. `REPORT`
  and `PRICE` are also not numeric scoring sources. If no scoring source is
  available, the worker can still publish an evidence row, but it should carry a
  warning/review state rather than a directional score.
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
- Output includes the bounded fair queue cycle call.

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
- Empty queues are valid and should show `total_runs=0`.

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
$headers = @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue" -Headers $headers
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/tasks?status=failed&limit=20" -Headers $headers
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

Check publish readiness:

```powershell
$health = Invoke-RestMethod -Method Get -Uri "http://localhost:8011/health"
$health.runtime.publishing
```

Expected:

- `status = ready` and `mode = backend_db` when `BACKEND_DATABASE_URL` is configured.
- `status = disabled` and `mode = single_db_noop` means `PUBLISH_SIGNALS` tasks are skipped and backend `api.*` tables will stay empty.

Enqueue DART collection:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/dart/collect" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"limit":100,"priority":"batch"}'
```

Enqueue Report collection:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/schedules/report/collect" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'
```

Drain queues manually:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/run-cycle" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_passes":10000}'
```

Check queue state:

```powershell
$headers = @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue" -Headers $headers
```

Check recent DART analysis:

```powershell
$headers = @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/dart/analysis-results?stock_code=005930&limit=5" -Headers $headers
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

Use internal network URLs. `/internal/*` endpoints fail closed when `INTERNAL_API_TOKEN` is empty,
and every scheduler call must send the same value as `X-Internal-Token`.

For Linux hosts without PowerShell, use `curl` in the same order:

```bash
WORKER_INTERNAL_URL="${WORKER_INTERNAL_URL:-http://127.0.0.1:8011}"
INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:?required}"

curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/schedules/dart/collect" \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"limit":100,"priority":"batch"}'

curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/schedules/report/collect" \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'

curl -fsS -X POST "$WORKER_INTERNAL_URL/internal/queue/run-cycle" \
  -H "X-Internal-Token: $INTERNAL_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"max_passes":10000}'
```

Cron sketch:

```cron
# Every 5 minutes: drain queues.
*/5 * * * * WORKER_INTERNAL_URL=http://127.0.0.1:8011 /opt/signal-alpha/services/agent-worker/ops/run-agent-pipeline-drain.sh

# Every hour during the day: enqueue DART collection.
0 8-20 * * * curl -fsS -X POST http://127.0.0.1:8011/internal/schedules/dart/collect -H "X-Internal-Token: $INTERNAL_API_TOKEN" -H 'Content-Type: application/json' -d '{"limit":100,"priority":"batch"}'

# Twice daily: enqueue Report collection.
15 8,17 * * * curl -fsS -X POST http://127.0.0.1:8011/internal/schedules/report/collect -H "X-Internal-Token: $INTERNAL_API_TOKEN" -H 'Content-Type: application/json' -d '{"limit":100,"days_back":7,"max_pages":20,"priority":"batch"}'
```

## 10. Managed Scheduler Example

For Cloud Scheduler, Railway Cron, GitHub Actions, or another managed scheduler:

1. Keep `agent-worker` running.
2. Point the scheduler to an internal or protected worker URL.
3. Trigger collection endpoints on a source-specific cadence.
4. Trigger `POST /internal/queue/run-cycle` every 1-5 minutes and send `X-Internal-Token`.
5. Monitor `/internal/stats/queue`, failed tasks, and dead letters.

Do not call `agent-worker` internal endpoints from a public GitHub Actions runner unless the URL is protected by VPN, private networking, a token-checking proxy, or equivalent access control.

## 11. Failure Handling

Expected behavior:

- Empty queues return `total_runs=0`.
- Individual task failures are reported in the cycle `statuses`/`failures` fields with a bounded `stopped_reason`.
- Retryable failures remain in `processing_queue`.
- Terminal failures are archived through the dead-letter path.

Operational checks:

```powershell
$headers = @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/stats/queue" -Headers $headers
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/tasks?status=failed&limit=20" -Headers $headers
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/queue/dead-letter?replayed=false" -Headers $headers
```

If failures are caused by missing API keys, fix environment variables and restart `agent-worker`.

## 12. Initial Rollout Recommendation

Start with this order:

1. Run the PowerShell script manually in `Drain` mode and confirm empty queues are harmless.
2. Run `Collect` mode with a small limit.
3. Run `Drain` mode with `MaxRuns=5`.
4. Increase `MaxRuns` after observing task duration and API usage.
5. Add OS or managed scheduler only after manual runs are stable.
