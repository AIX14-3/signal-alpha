# Agent Pipeline Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first operational scheduling layer for DART and Report collection/analysis by documenting the schedule policy, adding a local PowerShell runner, and describing Docker Compose and external cron execution.

**Architecture:** Keep collection, normalization, analysis, and aggregation inside `agent-worker` queue handlers. The scheduler layer only calls existing internal HTTP endpoints: `/internal/schedules/*` to enqueue collection work and `/internal/queue/{task_type}/run-batch` to drain queued tasks. This avoids moving collector/analyzer logic into `main-server` or `web`.

**Tech Stack:** FastAPI internal endpoints, PowerShell 5.1+, Docker Compose, external cron or Windows Task Scheduler.

---

### Task 1: Runbook

**Files:**
- Create: `docs/runbooks/agent-pipeline-schedule.md`

- [x] Document DART and Report collection schedules.
- [x] Document queue drain order from source-specific tasks through ML, aggregation, synthesis, and risk veto.
- [x] Document Docker Compose manual execution.
- [x] Document production scheduling through external cron or managed scheduler.

### Task 2: Local PowerShell Runner

**Files:**
- Create: `services/agent-worker/ops/run_agent_pipeline_schedule.ps1`

- [x] Add parameters for worker base URL, schedule mode, DART/Report limits, Report date window, max batch size, and timeout.
- [x] Implement JSON POST helper with clear failure output.
- [x] Call DART and Report schedule endpoints when collection is enabled.
- [x] Drain queue task types in deterministic order using returned HTTP responses.

### Task 3: Verification

**Commands:**
- `powershell -NoProfile -ExecutionPolicy Bypass -Command "$tokens=$null;$errors=$null;$null=[System.Management.Automation.Language.Parser]::ParseFile('services/agent-worker/ops/run_agent_pipeline_schedule.ps1',[ref]$tokens,[ref]$errors); if($errors.Count){$errors | ForEach-Object { Write-Error $_ }; exit 1 }"`
- `git diff --check`

**Expected:** PowerShell parser returns no syntax errors, and `git diff --check` reports no whitespace errors.
