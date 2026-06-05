# Agent Worker

Worker service for data collection and LLM-based analysis.

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
