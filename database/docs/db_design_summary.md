# Signal Alpha DB Design Summary

## Report Roles

리포트는 수집(PDF)·파싱·정규화까지만 수행한다. 임베딩/RAG 분석(`report_chunks`,
`pgvector`)은 제거됐다.

### Report Collector

- Stores report metadata and parsing status in `report_raw_details`.
- Downloads the report PDF or stores its local file path.
- Extracts text from the PDF.
- Does not call an LLM.

### Report Normalizer

- Promotes parsed reports to `signal_events` / `signal_metrics` (canonical path).
- 임베딩/RAG 분석은 제거됨(이전 BGE-M3 임베딩 + pgvector 유사도 검색 폐지).

## Array Column Limits

The following columns are PostgreSQL arrays, so PostgreSQL cannot enforce foreign key integrity for each element:

- `processing_queue.source_raw_ids`
- `processing_queue.source_signal_event_ids`
- `processing_queue.source_analysis_result_ids`
- `analysis_results.source_signal_event_ids`
- `agent_results.source_signal_event_ids`

For the MVP, arrays are kept for fast implementation. After the data model stabilizes, split them into mapping tables such as `analysis_signal_events` and `agent_signal_events`.

## MVP Service Scope

Initial service code should focus on this required flow:

```text
stocks
-> raw_documents
-> report_raw_details
-> source_documents
-> signal_events
-> signal_metrics
-> analysis_results
-> agent_results
-> final_signals
```

The following tables are expansion-stage tables:

- `quant_scores`
- `ta_scores`
- `ai_scores`
- `xgb_model_versions`
- `ml_scores`
- `score_history`
- `backtest_results`
- `user_sessions`
- `social_accounts`
- `portone_verifications`
- `terms_agreements`
- `admin_accounts`
- `admin_sessions`
