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
Collector.collect(stock_code)
  -> list[RawEvidence]
Analyzer.analyze(stock_code, evidence)
  -> SourceResult
AgentOrchestrator.run(stock_code)
  -> dict[source, SourceResult]
```
