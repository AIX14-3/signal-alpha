# Signal Alpha Claude Guide

## Project Context

Signal Alpha is a multi-source investment signal service. Collectors gather raw data, Normalizers convert raw data into structured events and metrics, and Agents analyze normalized evidence.

## Mandatory Collector Boundary

### DB NOT NULL required

These columns are `NOT NULL` in the migration and must always be provided:

- `stock_id`
- `source_type`
- `source_name`
- `external_id`
- `source_hash`
- `title`
- `published_at`

### Project required

These columns are required by project policy even if DB allows NULL or has a DEFAULT:

- `collector_run_id` — nullable in DB, but must always be set by Collector
- `collect_status` — has DEFAULT `'success'`, but Collector must set explicitly
- `collector_ver` — has DEFAULT `'1.0'`, but Collector must set from `COLLECTOR_VERSION` env var

Collectors may write only to:

- `collector_runs`
- `raw_documents`
- source-specific raw detail tables:
  - `patent_raw_details`
  - `datalab_raw_details`
- `processing_queue`

Collectors must not write to:

- `source_documents`
- `signal_events`
- `signal_metrics`
- `analysis_results`
- `agent_results`
- `final_signals`

Collectors must not call LLMs. They collect, store, and enqueue only.

## Keyword Generator Boundary

Keyword generation is a separate pre-collection tool. It may use Gemini 2.5 to
draft DataLab categories and keywords for a stock, but the draft must be saved
for review before being applied to `category_registry.json` or the DB.

Collectors must never import, call, or depend on the keyword generator. They must
never call Gemini or any other LLM.

## Source Hash Contract

Generate `source_hash` in application code before inserting `raw_documents`.

Normalization rules:

- SHA256
- 64-character hex string
- trim every part
- lower-case every possible part
- convert NULL/None to empty string
- join parts with `|`

Collector-specific keys:

- Patent: `PATENT|application_no`
- DataLab: `DATALAB|category_id|keyword|observed_date|period_type|device|gender|age_group`

## Patent Collector

Required raw flow:

```text
collector_runs
-> raw_documents(source_type='PATENT')
-> patent_raw_details
-> processing_queue(task_type='NORMALIZE_PATENT')
```

Required detail fields:

- `raw_document_id`
- `stock_id`
- `application_no`
- `patent_title`
- `applicant_name`
- `application_date`
- `tech_category`
- `is_new_category`
- `extra_payload`

## DataLab Collector

Design principle: DataLab data is collected by **category** (search theme), not by stock.
The Normalizer maps categories to stocks via `datalab_category_stocks`.

Required raw flow:

```text
collector_runs
-> datalab_raw_documents(category_id)   ← NOT raw_documents
-> datalab_raw_details(category_id)     ← category_id instead of stock_id
-> processing_queue(task_type='NORMALIZE_DATALAB', stock_id=NULL)
```

Default values:

- `period_type = daily`
- `device = all`
- `gender = all`
- `age_group = all`

Naver API timeUnit ↔ DB period_type mapping:

| DB period_type | Naver API timeUnit |
| --- | --- |
| `daily` | `date` |
| `weekly` | `week` |
| `monthly` | `month` |

Never store NULL for `device`, `gender`, `age_group`; always use `all`.

Required detail fields:

- `raw_document_id` (from `datalab_raw_documents`)
- `category_id` (replaces stock_id)
- `keyword`
- `keyword_group`
- `observed_date`
- `search_index`
- `previous_search_index`
- `change_pct`
- `period_type`
- `device`
- `gender`
- `age_group`
- `is_spike`
- `extra_payload`

## Error Handling

Collector run status:

- `success`: `inserted_count > 0 and failed_count = 0`, OR `inserted_count = 0 and skipped_count > 0 and failed_count = 0` (all-duplicate-skip runs also count as success)
- `partial`: at least one record inserted or skipped, and at least one record failed
- `failed`: no usable record inserted or skipped due to fatal failure

Duplicate source records are normal operational skips, not hard failures.

## Transaction Rule

For each record, insert `raw_documents`, detail table, and `processing_queue` in one transaction.
If queue registration fails, rollback `raw_documents` and detail insert, then increment `failed_count`.
Do not leave raw/detail rows without a corresponding `processing_queue` entry.

## Duplicate Handling Rule

Any `UniqueViolation` is a skip, not a failure.
Rollback the full transaction, then increment `skipped_count`.

## task_context Rule

`task_context` is nullable in DB, but project-required for all Collector tasks.
Always include `collector_run_id`, `source_type`, `collector_ver`.

## Environment Variables

Never hardcode API keys.

Recommended variables:

- `DATABASE_URL`
- `KIPRIS_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `COLLECTOR_VERSION`
- `GEMINI_API_KEY` — keyword generator only, never used by collectors
- `GEMINI_MODEL` — optional keyword generator model override

## Development Workflow (Issue → Branch → PR)

For any **substantial** code change that goes to the repository, follow this flow:

1. **Register a Task issue** in the "Final Project - Signal Alpha" GitHub Project before starting.
   Use the repo template: `## 배경` / `## 범위` / `## 작업 내용` / `## 완료 기준` / `## 의존(선행)` / `## 비범위`,
   plus a `<!-- kickoff-branch:<branch> -->` marker at the bottom. Labels: `task` + `area:*` + `P0~P3`.
2. **Work on the linked branch** — `feat/<topic>` or `fix/<topic>` created for that issue, never directly on `main`.
3. **PR body** must include `Closes #<issue>`.

**Threshold:** Truly trivial edits (typo, one-liner, comment) are exempt — no issue required.
A **large structural change or significant change** must have an issue + linked branch.

When in doubt about whether a change crosses the threshold, register the issue.

**Bundle PRs.** The repo has a single merge owner — do NOT open many tiny (1–5 file) PRs.
Group changes of similar nature (same area/module/theme) into one PR + one child issue.
Plan work in bundles up front; consolidate already-open PRs when they can be grouped.
Separate a risky behavior change (🔴) from pure cleanups only when it genuinely aids review.

**Scope: alternative data only.** Work within HIRING · PATENT · DATALAB · aggregator.
Do NOT modify DART (`app/**/dart/**`), main-server, or other teammates' areas — high merge-conflict risk.
Exception: a genuinely broken or truly essential cross-cutting fix — and only after flagging it to the user first.
Shared code (`orchestrator/persistence.py`, `packages/signal-core`, `packages/data-access`) is OK when alternative-data work needs it, kept minimal.

## Self-Review Before Reporting Done

For any **substantial** code change (same threshold as the Issue→Branch→PR rule —
not trivial typo/one-liner/comment edits), run `/code-review low` on the working
diff and read its findings BEFORE telling the user the work is done. Surface the
real findings in your summary, with what you fixed and what you deliberately left.

- **Stage new files first.** `/code-review` only sees the git diff, so brand-new
  (untracked) files are invisible to it. Run `git add` on new files before
  reviewing, or they fall into a review blind spot.
- **Scope:** alternative-data areas only; skip for trivial edits.
- This is a self-check, not a gate — it does not replace the human PR review the
  merge owner performs.
