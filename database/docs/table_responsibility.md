# 테이블 책임 분리

| 역할 | 사용하는 테이블 | 규칙 |
| --- | --- | --- |
| DART Collector | `collector_runs`, `raw_documents`, `dart_raw_details`, `processing_queue` | LLM 호출 없음 |
| Report Collector | `collector_runs`, `raw_documents`, `report_raw_details`, `processing_queue` | PDF 다운로드 또는 로컬 경로 저장<br>PDF 텍스트 추출(`report_raw_details.extracted_text`)<br>LLM 호출 없음 |
| Report Normalizer | `source_documents`, `signal_events`, `signal_metrics`, `validation_logs` | 파싱 리포트를 canonical 이벤트/메트릭으로 승격 (임베딩/RAG 분석 제거됨) |
| Hiring Collector | `collector_runs`, `raw_documents`, `hiring_raw_details`, `processing_queue` | `stock_id` 필수 저장 |
| Patent Collector | `collector_runs`, `raw_documents`, `patent_raw_details`, `processing_queue` | `stock_id` 필수 저장 |
| DataLab Collector | `collector_runs`, `raw_documents`, `datalab_raw_details`, `processing_queue` | `period_type`, `device`, `gender`, `age_group` 기본값 필수 |
| Price Collector | `ohlcv_data` | 장 마감 후 수집 |
| Sector Collector | `sectors`, `sector_ohlcv` | 업종/종합지수 raw 적재<br>상대강도 등 파생 지표 계산 금지 (signal_metrics 에서 계산) |
| Normalizer | `source_documents`, `signal_events`, `signal_metrics`, `validation_logs` | 수치 데이터는 DB 값 고정 |
| Agent | `analysis_results`, `agent_results`, `validation_logs` | Raw 직접 조회 금지<br>정규화된 `signal_events`와 `signal_metrics` 기반 분석 |
| ML | `ml_scores`, `xgb_model_versions`, `score_history`, `backtest_results` | 내부 검증 및 보정 |
| Frontend | `final_signals`, `source_documents`, `signal_events`, `signal_metrics` | `final_signals` 중심 조회 |
