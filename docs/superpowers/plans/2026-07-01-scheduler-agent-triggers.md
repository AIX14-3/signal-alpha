# Scheduler Agent Triggers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the DB-backed scheduler agent trigger Report collection as well as DART and Price, while preserving service boundaries and queue-drain ownership.

**Architecture:** Keep `run_scheduler_instance.py` as a trigger orchestrator only. It reads `collection_schedules`, calls existing `agent-worker` internal endpoints with `X-Internal-Token`, records per-target summaries, and leaves collection, normalization, analysis, aggregation, synthesis, and publishing to existing worker handlers and the queue drain daemon.

**Tech Stack:** Python, pytest, httpx-compatible async client calls, FastAPI internal endpoints, Markdown runbooks.

---

## File Structure

- Modify: `services/agent-worker/run_scheduler_instance.py`
  - Add Report defaults and a `report` branch inside `_fire()`.
  - Update the module docstring so the documented target list matches the implementation.
- Create: `services/agent-worker/tests/test_scheduler_instance.py`
  - Unit-test `_fire()` without a running worker by using a fake async HTTP client.
  - Cover Report default payload, independent target execution, and partial status.
- Modify: `services/agent-worker/tests/test_worker_runtime_config.py`
  - Add assertions that the runbook documents DB scheduler agent behavior and current queue drain task names.
- Modify: `docs/runbooks/agent-pipeline-schedule.md`
  - Update the runbook from external-scheduler-first language to the current DB-backed scheduler agent model.
  - Replace stale queue task names with the current queue drain daemon plan.

## Task 1: Scheduler Agent Unit Tests

**Files:**
- Create: `services/agent-worker/tests/test_scheduler_instance.py`

- [ ] **Step 1: Create failing tests for Report target trigger behavior**

Create `services/agent-worker/tests/test_scheduler_instance.py` with this exact content:

```python
import httpx
import pytest

from run_scheduler_instance import _fire, _overall_status


class FakeResponse:
    def __init__(self, payload, *, status_code=200, url="http://worker/test"):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} error",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class RecordingClient:
    def __init__(self, *, failures=None):
        self.posts = []
        self.failures = set(failures or [])

    async def post(self, url, *, json, headers, timeout):
        path = url.removeprefix("http://worker")
        self.posts.append(
            {
                "path": path,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if path in self.failures:
            return FakeResponse({"error": "boom"}, status_code=500, url=url)
        if path == "/internal/schedules/dart/collect":
            return FakeResponse({"scheduled_count": 2}, url=url)
        if path == "/internal/schedules/report/collect":
            return FakeResponse({"scheduled_count": 3}, url=url)
        if path == "/internal/price/collect":
            return FakeResponse({"status": "ok"}, url=url)
        raise AssertionError(f"Unexpected path: {path}")


@pytest.mark.asyncio
async def test_fire_calls_report_collect_with_default_batch_payload(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient()

    summary = await _fire(
        client,
        base_url="http://worker",
        schedule={"targets": ["report"]},
    )

    assert summary == {"report": 3}
    assert client.posts == [
        {
            "path": "/internal/schedules/report/collect",
            "json": {
                "limit": 100,
                "days_back": 7,
                "max_pages": 20,
                "priority": "batch",
            },
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 120.0,
        }
    ]


@pytest.mark.asyncio
async def test_fire_records_each_target_and_continues_after_report_failure(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient(failures={"/internal/schedules/report/collect"})

    summary = await _fire(
        client,
        base_url="http://worker/",
        schedule={
            "targets": ["dart", "report", "price"],
            "dart_limit": 5,
            "price_modes": ["snapshot"],
        },
    )

    assert summary["dart"] == 2
    assert summary["report"].startswith("error: ")
    assert summary["price"] == {"snapshot": "ok"}
    assert [post["path"] for post in client.posts] == [
        "/internal/schedules/dart/collect",
        "/internal/schedules/report/collect",
        "/internal/price/collect",
    ]
    assert _overall_status(summary) == "partial"
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```powershell
cd services/agent-worker
uv run pytest -q tests/test_scheduler_instance.py
```

Expected result before implementation:

```text
FAILED tests/test_scheduler_instance.py::test_fire_calls_report_collect_with_default_batch_payload
FAILED tests/test_scheduler_instance.py::test_fire_records_each_target_and_continues_after_report_failure
```

The failure should show that `summary` has no `report` key because `_fire()` does not handle the Report target yet.

## Task 2: Report Trigger Implementation

**Files:**
- Modify: `services/agent-worker/run_scheduler_instance.py`
- Test: `services/agent-worker/tests/test_scheduler_instance.py`

- [ ] **Step 1: Add Report defaults near existing scheduler constants**

In `services/agent-worker/run_scheduler_instance.py`, change the constants block to:

```python
DEFAULT_BASE_URL = "http://localhost:8011"
DEFAULT_SCHEDULE_NAME = "daily-collection"
DEFAULT_REPORT_LIMIT = 100
DEFAULT_REPORT_DAYS_BACK = 7
DEFAULT_REPORT_MAX_PAGES = 20
DEFAULT_PRIORITY = "batch"
```

- [ ] **Step 2: Update the module docstring target list**

In the top module docstring of `services/agent-worker/run_scheduler_instance.py`, replace the target bullet list with:

```text
  - price  → POST /internal/price/collect (mode 별: flows, snapshot)
  - dart   → POST /internal/schedules/dart/collect (limit, priority=batch)
  - report → POST /internal/schedules/report/collect (limit, days_back, max_pages, priority=batch)
```

- [ ] **Step 3: Use `DEFAULT_PRIORITY` for DART**

Inside `_fire()`, change the DART payload from:

```python
{"limit": int(schedule.get("dart_limit") or 10), "priority": "batch"},
```

to:

```python
{"limit": int(schedule.get("dart_limit") or 10), "priority": DEFAULT_PRIORITY},
```

- [ ] **Step 4: Add the Report target branch**

Inside `_fire()`, after the DART block and before the Price block, add:

```python
    if "report" in targets:
        try:
            report = await _post(
                "/internal/schedules/report/collect",
                {
                    "limit": DEFAULT_REPORT_LIMIT,
                    "days_back": DEFAULT_REPORT_DAYS_BACK,
                    "max_pages": DEFAULT_REPORT_MAX_PAGES,
                    "priority": DEFAULT_PRIORITY,
                },
            )
            summary["report"] = (
                report.get("scheduled_count") if isinstance(report, dict) else report
            )
        except Exception as exc:  # noqa: BLE001 - 한 대상 실패가 다른 대상/상태기록을 막지 않게
            logger.warning("report/collect 실패: %s", exc)
            summary["report"] = f"error: {exc}"
```

- [ ] **Step 5: Run scheduler tests and confirm they pass**

Run:

```powershell
cd services/agent-worker
uv run pytest -q tests/test_scheduler_instance.py tests/test_scheduler_internal_auth.py
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Commit the scheduler code and tests**

Run:

```powershell
git add services/agent-worker/run_scheduler_instance.py services/agent-worker/tests/test_scheduler_instance.py
git commit -m "feat: trigger report collection from scheduler agent"
```

## Task 3: Runbook Contract Tests

**Files:**
- Modify: `services/agent-worker/tests/test_worker_runtime_config.py`
- Modify: `docs/runbooks/agent-pipeline-schedule.md`

- [ ] **Step 1: Add runbook assertions first**

In `services/agent-worker/tests/test_worker_runtime_config.py`, inside `test_worker_runbooks_match_current_queue_and_auth_contracts()`, add these assertions after the existing `assert "Queue cycle execution | ..."` line:

```python
    assert "DB-backed scheduler agent" in schedule
    assert "run_scheduler_instance.py" in schedule
    assert "Price collection trigger | `POST /internal/price/collect`" in schedule
    assert "Report collection enqueue | `POST /internal/schedules/report/collect`" in schedule
    assert "publish_signals" in schedule
    assert "ml_infer" not in schedule
    assert "meta_combine" not in schedule
    assert "risk_veto" not in schedule
```

- [ ] **Step 2: Run the contract test and confirm it fails**

Run:

```powershell
cd services/agent-worker
uv run pytest -q tests/test_worker_runtime_config.py::test_worker_runbooks_match_current_queue_and_auth_contracts
```

Expected result before runbook edits:

```text
FAILED tests/test_worker_runtime_config.py::test_worker_runbooks_match_current_queue_and_auth_contracts
```

The failure should point to missing DB scheduler wording or stale queue task names.

## Task 4: Runbook Update

**Files:**
- Modify: `docs/runbooks/agent-pipeline-schedule.md`
- Test: `services/agent-worker/tests/test_worker_runtime_config.py`

- [ ] **Step 1: Update the runbook date and intro**

At the top of `docs/runbooks/agent-pipeline-schedule.md`, replace:

```markdown
Updated: 2026-06-25

This runbook describes how to run DART and Report collection, normalization, analysis, and final data-direction aggregation on a schedule.
```

with:

```markdown
Updated: 2026-07-01

This runbook describes how to run Price, DART, and Report collection triggers, queue draining, analysis, and final data-direction aggregation on a schedule.
```

- [ ] **Step 2: Update the internal endpoint table**

Replace the "Existing internal endpoints" table with:

```markdown
Existing internal endpoints:

| Purpose | Endpoint |
|---|---|
| Price collection trigger | `POST /internal/price/collect` |
| DART collection enqueue | `POST /internal/schedules/dart/collect` |
| Report collection enqueue | `POST /internal/schedules/report/collect` |
| Queue cycle execution | `POST /internal/queue/run-cycle` |
```

- [ ] **Step 3: Replace the MVP scheduling model section**

Replace the current section `## 2. Recommended MVP Scheduling Model` through the paragraph that starts with `Avoid enabling` with:

```markdown
## 2. Recommended MVP Scheduling Model

Use the DB-backed scheduler agent for managed operations.

The scheduler agent is `services/agent-worker/run_scheduler_instance.py`. It polls the backend-owned `collection_schedules` table, evaluates `enabled`, `run_at_local`, `timezone`, `targets`, and `manual_trigger_requested_at`, then calls only internal `agent-worker` endpoints. It records `last_run_at`, `last_status`, `last_detail`, and `next_run_at` back to the same row.

Recommended choices:

- Local development: manual PowerShell script for smoke tests, or `uv run python run_scheduler_instance.py --once` for one DB-backed evaluation.
- Managed deployment: one scheduler deployment running `python run_scheduler_instance.py`, as shown in `deploy/k8s/scheduler.yaml`.
- Emergency/manual operations: admin schedule "trigger" updates `manual_trigger_requested_at`; the scheduler agent fires it on the next polling cycle.

Keep exactly one scheduler agent active for a schedule row. Multiple scheduler replicas can enqueue duplicate collection work unless a distributed lock is added.
```

- [ ] **Step 4: Replace the queue drain order section**

Replace section `## 4. Queue Drain Order` through the paragraph ending with `scheduled window.` with:

````markdown
## 4. Queue Drain Order

The scheduler agent only enqueues or triggers collection. Queue consumption is owned by the worker queue drain daemon (`QUEUE_DRAIN_DAEMON_ENABLED`) and by the same bounded fair cycle exposed at `POST /internal/queue/run-cycle`.

The current default fair-cycle plan is:

```text
collect_dart
collect_report
normalize_dart
analyze_dart
process_report
normalize_report
analyze_report
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
aggregate_signal
synthesize
publish_signals
```

Why this order:

- DART: `collect_dart -> normalize_dart -> analyze_dart`
- Report: `collect_report -> process_report -> normalize_report -> analyze_report`
- PRICE: scheduler triggers collection; `analyze_price` reads DB data only.
- Alternative data: external collection jobs feed normalize, enrich, and per-source analyze tasks.
- Downstream tasks infer source returns, combine return evidence, aggregate source results, synthesize user-facing data-direction narratives, and publish approved outputs.

Each queue type is idempotent through existing dedupe and upsert behavior. The fair-cycle runner processes at most one task per type per pass, so one source cannot monopolize a scheduled window.
````

- [ ] **Step 5: Run the runbook contract test and confirm it passes**

Run:

```powershell
cd services/agent-worker
uv run pytest -q tests/test_worker_runtime_config.py::test_worker_runbooks_match_current_queue_and_auth_contracts
```

Expected:

```text
1 passed
```

- [ ] **Step 6: Commit the runbook and contract test**

Run:

```powershell
git add docs/runbooks/agent-pipeline-schedule.md services/agent-worker/tests/test_worker_runtime_config.py
git commit -m "docs: document scheduler agent trigger model"
```

## Task 5: Final Verification

**Files:**
- Verify: `services/agent-worker/run_scheduler_instance.py`
- Verify: `services/agent-worker/tests/test_scheduler_instance.py`
- Verify: `services/agent-worker/tests/test_scheduler_internal_auth.py`
- Verify: `services/agent-worker/tests/test_worker_runtime_config.py`
- Verify: `docs/runbooks/agent-pipeline-schedule.md`

- [ ] **Step 1: Run focused scheduler and docs tests**

Run:

```powershell
cd services/agent-worker
uv run pytest -q tests/test_scheduler_instance.py tests/test_scheduler_internal_auth.py tests/test_worker_runtime_config.py
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run whitespace check**

Run from repository root:

```powershell
git diff --check
```

Expected:

```text
no output and exit code 0
```

- [ ] **Step 3: Inspect final diff**

Run:

```powershell
git diff --stat HEAD~2..HEAD
git status --short
```

Expected:

```text
The diff shows scheduler code, scheduler tests, runbook updates, and runtime config test updates.
git status --short prints no unstaged or staged files.
```

- [ ] **Step 4: Prepare PR summary**

Use this PR summary:

```markdown
## Summary

- Add Report collection triggering to the DB-backed scheduler agent.
- Preserve independent per-target failure handling and `last_detail` summaries.
- Update scheduler runbook coverage for the current queue drain daemon and trigger model.

## Tests

- `uv run pytest -q tests/test_scheduler_instance.py tests/test_scheduler_internal_auth.py tests/test_worker_runtime_config.py`
- `git diff --check`
```
