# Data Flow

```text
1. User requests analysis from the frontend.
2. Main server creates an analysis job.
3. Agent worker collects raw evidence.
4. Agent worker analyzes evidence with source-specific analyzers.
5. Agent worker aggregates source results.
6. Main server exposes the final result to the frontend.
```

Collection and analysis should remain separate:

- Collectors store `raw_evidence`.
- Analyzers store `analysis_results`.
- Job status is tracked separately in `analysis_jobs`.
