# Agent Worker

Worker service for data collection and LLM-based analysis.

## Local Development

```bash
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --port 8011
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

The first DART collector scope collects the OpenDART disclosure list only.
It follows OpenDART pagination by fetching page 1 first and then continuing through
`total_page` with the configured `DART_PAGE_SIZE`.

```text
collect_dart task
  -> dart_corp_codes lookup by ticker
  -> OpenDART /list.json
  -> RawEvidence(source="DART")
  -> raw_documents + dart_raw_details
  -> normalize_dart queue task
```

Required environment values:

```text
DART_API_KEY=
DART_BASE_URL=https://opendart.fss.or.kr/api
DART_TIMEOUT_SECONDS=10
DART_PAGE_SIZE=100
```

The `collect_dart` task expects `processing_queue.task_context` to include `stock_code`.
Optional date filters use OpenDART parameter names:

```json
{
  "stock_code": "005930",
  "bgn_de": "20260601",
  "end_de": "20260608"
}
```

Queue a DART collection task through the worker API:

```powershell
$body = '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","bgn_de":"20260601","end_de":"20260608"}}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8011/internal/tasks/collect_dart/enqueue" -ContentType "application/json" -Body $body
```

The enqueue endpoint deduplicates by default. If an active `pending`, `running`, or `retrying`
task already exists with the same `stock_id`, `task_type`, and `task_context`, the endpoint
returns the existing `task_id` instead of inserting another queue row.

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
