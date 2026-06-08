# 테이블 책임 분리

| 역할 | 사용하는 테이블 | 규칙 |
| --- | --- | --- |
| DART Collector | `collector_runs`, `raw_documents`, `dart_raw_details`, `processing_queue` | LLM 호출 없음 |
| Report Collector | `collector_runs`, `raw_documents`, `report_raw_details`, `report_chunks`, `processing_queue` | PDF 다운로드 또는 로컬 경로 저장<br>PDF 텍스트 추출<br>`report_chunks.chunk_text` 저장<br>LLM 호출 없음 |
| Embedding Worker | `report_chunks` | `embedding IS NULL`인 chunk 조회<br>BGE-M3 등 embedding 모델로 벡터 생성<br>`report_chunks.embedding` 업데이트 |
| Report Analyst | `report_chunks`, `signal_events`, `signal_metrics`, `analysis_results`, `agent_results` | pgvector similarity search 수행<br>검색된 chunk를 근거로 LLM 분석<br>`signal_events`, `signal_metrics`, `analysis_results`, `agent_results` 저장 |
| Hiring Collector | `collector_runs`, `raw_documents`, `hiring_raw_details`, `processing_queue` | `stock_id` 필수 저장 |
| Patent Collector | `collector_runs`, `raw_documents`, `patent_raw_details`, `processing_queue` | `stock_id` 필수 저장 |
| DataLab Collector | `collector_runs`, `raw_documents`, `datalab_raw_details`, `processing_queue` | `period_type`, `device`, `gender`, `age_group` 기본값 필수 |
| Price Collector | `ohlcv_data` | 장 마감 후 수집 |
| Sector Collector | `sectors`, `sector_ohlcv` | 업종/종합지수 raw 적재<br>상대강도 등 파생 지표 계산 금지 (signal_metrics 에서 계산) |
| Normalizer | `source_documents`, `signal_events`, `signal_metrics`, `validation_logs` | 수치 데이터는 DB 값 고정 |
| Agent | `analysis_results`, `agent_results`, `validation_logs` | Raw 직접 조회 금지<br>정규화된 `signal_events`와 `signal_metrics` 기반 분석 |
| ML | `ml_scores`, `xgb_model_versions`, `score_history`, `backtest_results` | 내부 검증 및 보정 |
| Frontend | `final_signals`, `source_documents`, `signal_events`, `signal_metrics` | `final_signals` 중심 조회 |
