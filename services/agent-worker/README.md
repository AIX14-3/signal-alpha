# Agent Worker

Worker service for data collection and LLM-based analysis.

## Local Development

```bash
uv sync --package signal-alpha-agent-worker --extra dev
uv run --package signal-alpha-agent-worker uvicorn app.main:app --reload --port 8011
```

Health check:

```text
GET /health
```

Expected response:

```json
{
  "status": "ok",
  "service": "agent-worker",
  "version": "0.1.0"
}
```

## Responsibilities

- Collect raw source data.
- Normalize collected evidence.
- Run source-specific analyzers.
- Run final aggregation/debate analysis.
- Validate LLM outputs against shared schemas.

## Internal Structure

```text
app/
  collectors/      # DART, report, alternative data collection
  analyzers/       # LLM/RAG analysis and source scoring
  orchestrator/    # End-to-end agent run coordination
  schemas/         # Worker-local schemas
  prompts/         # Prompt templates and prompt version notes
```

## Worker Boundaries

The worker keeps collection and LLM analysis separate.

- Collectors return `RawEvidence` only.
- Collectors must not create `direction`, `score`, or `summary`.
- Analyzers receive collected `RawEvidence` and return `SourceResult`.
- Analyzers must not call external source APIs directly.
- The orchestrator connects collectors to analyzers and combines source results.

Pipeline flow:

```text
await Collector.collect(stock_code)
  -> list[RawEvidence]
await Analyzer.analyze(stock_code, evidence)
  -> SourceResult
await AgentOrchestrator.run(stock_code)
  -> dict[source, SourceResult]
```

## DART Collection

The DART collector fetches OpenDART disclosure lists and, by default, downloads each disclosure
document ZIP through `document.xml`. It follows OpenDART pagination by fetching page 1 first and
then continuing through `total_page` with the configured `DART_PAGE_SIZE`.

```text
collect_dart task
  -> dart_corp_codes lookup by ticker
  -> OpenDART /list.json
  -> OpenDART /document.xml by receipt number
  -> RawEvidence(source="DART", content=document_text)
  -> raw_documents + dart_raw_details
  -> normalize_dart queue task
```

Required environment values:

```text
DART_API_KEY=
DART_BASE_URL=https://opendart.fss.or.kr/api
DART_TIMEOUT_SECONDS=10
DART_PAGE_SIZE=100
DART_FETCH_DOCUMENTS=true
DART_MAX_RETRIES=2
DART_RETRY_BACKOFF_SECONDS=0.5
```

Set `DART_FETCH_DOCUMENTS=false` to collect disclosure list metadata only.
Retryable DART failures such as request-limit responses (`020`), maintenance (`800`), undefined
server errors (`900`), HTTP 429/5xx, and transient network failures are retried with exponential
backoff. Auth/IP/key failures such as `010`, `011`, `012`, and `901` fail without retry.
Disclosure document download failures do not fail the whole list collection; the worker stores
`document_fetch_status`, `document_error_category`, and retryability metadata in the DART raw
detail payload.

The `collect_dart` task expects `processing_queue.task_context` to include `stock_code`.
Optional date filters use OpenDART parameter names:

```json
{
  "stock_code": "005930",
  "bgn_de": "20260601",
  "end_de": "20260608"
}
```

If `bgn_de` is omitted, the worker reads `dart_collection_states` and starts from the day after
the last successful `last_end_de`. If no state exists yet, it uses a 30-day lookback from `end_de`.
After a successful collection, it updates the state with the resolved date window, collected count,
collector run id, and last receipt number.

DART duplicate and correction policy:

- Repeated collection of the same `receipt_no` updates the same raw document through
  `(source_type, external_id)` upsert.
- Correction disclosures are not merged into the original disclosure. They are stored as separate
  raw documents and separate `correction` signal events.
- When OpenDART provides an original receipt number, it is stored as `original_receipt_no`.
- Correction disclosures use `priority="immediate"` and `needs_review=true` after normalization.

Queue a DART collection task through the worker API:

```powershell
$body = '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","bgn_de":"20260601","end_de":"20260608"}}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/tasks/collect_dart/enqueue" -ContentType "application/json" -Body $body
```

The enqueue endpoint deduplicates by default. If an active `pending`, `running`, or `retrying`
task already exists with the same `stock_id`, `task_type`, and `task_context`, the endpoint
returns the existing `task_id` instead of inserting another queue row.

To intentionally re-run normalization and analysis for already collected DART documents, include
`"force_reprocess":true` in `task_context`:

```powershell
$body = '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","bgn_de":"20260601","end_de":"20260608","force_reprocess":true}}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/tasks/collect_dart/enqueue" -ContentType "application/json" -Body $body
```

Query stored DART analysis results:

```powershell
Invoke-RestMethod -Method Get -Uri "http://localhost:8011/internal/dart/analysis-results?stock_code=005930&analysis_date=2026-06-08"
```

Run a development E2E pass that collects, normalizes, analyzes, and returns stored DART analysis
results:

```powershell
$body = '{"stock_id":1,"stock_code":"005930","bgn_de":"2026-06-01","end_de":"2026-06-08","force_reprocess":true}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/dart/e2e/run" -ContentType "application/json" -Body $body
```

Delete development DART test data for a stock and date range:

```powershell
Invoke-RestMethod -Method Delete -Uri "http://localhost:8011/internal/dart/test-data?stock_code=005930&bgn_de=2026-06-01&end_de=2026-06-30"
```

Quarterly and annual report text is scanned for basic financial figures such as revenue,
operating profit, and net income. Extracted values are stored in `signal_metrics` as
`KRW_million` metrics when recognizable values are present.

### DART Collection Schedule

An external cron or operations script can enqueue DART collection tasks for active stocks:

```powershell
$body = '{"limit":100,"end_de":"20260610","priority":"batch"}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/schedules/dart/collect" -ContentType "application/json" -Body $body
```

The schedule endpoint enqueues `collect_dart` tasks only. Actual analysis can run on a separate
schedule by claiming/running `normalize_dart` and later analysis tasks independently.

### DART Corp Code Sync

OpenDART corporation codes are synchronized from `GET /corpCode.xml`. The API returns a ZIP
file containing XML entries. The worker stores listed entries that include `stock_code`, because
`dart_corp_codes` is used as the ticker-to-corp-code mapping for stock collection.

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/dart/corp-codes/sync"
```

Expected response:

```json
{
  "fetched_count": 100000,
  "listed_count": 3000,
  "upserted_count": 3000
}
```

## Queue Maintenance

Stale queue tasks can be swept through the worker API:

```powershell
$body = '{"running_timeout_minutes":30,"retrying_timeout_minutes":120}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/queue/sweep-stale" -ContentType "application/json" -Body $body
```

- Old `running` tasks are moved to `retrying` when retry budget remains.
- Old `running` or exhausted old `retrying` tasks are moved to `failed`.
