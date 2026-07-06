# Report 파이프라인 스모크 테스트 런북

최종 갱신일: 2026-07-06

이 문서는 로컬 Docker 환경에서 증권사 리포트 수집부터 최종 Aggregator 반영까지 한 번에 확인하는 절차입니다.

Signal Alpha는 투자 추천 서비스가 아닙니다. 이 런북은 리포트 원문에서 구조화된 밸류에이션 fact를 만들고, 데이터 방향성, 근거, 소스 간 일치도, 추가 확인 필요 여부를 검증하는 절차만 다룹니다. PDF 원문, 긴 원문 텍스트, RAG 청크는 사용자-facing 결과로 노출하지 않습니다.

## 1. 범위

검증 대상 queue 흐름:

```text
collect_report
-> process_report
-> normalize_report
-> analyze_report
-> aggregate_signal
```

확인 대상 DB 산출물:

- `raw_documents`
- `report_raw_details`
- `report_valuation_facts`
- `source_documents`
- `signal_events`
- `signal_metrics`
- `analysis_results`
- `agent_results`
- `final_signals.score_breakdown.REPORT`

제외 범위:

- `embed_report`
- RAG retriever
- Report Agent 합성
- 원문 PDF 또는 긴 verbatim 청크 사용자 노출

## 2. 전제

- Docker compose로 `postgres`, `agent-worker`가 실행 중이어야 합니다.
- DB migration과 seed가 적용되어 `stocks`에 테스트 종목이 있어야 합니다.
- 로컬 테스트에서는 `REPORT_STORAGE_BACKEND=local`을 권장합니다.
- Gemini 보강을 확인하려면 `REPORT_USE_LLM=true`, `REPORT_LLM_PROVIDER=gemini`, `GEMINI_API_KEY`가 필요합니다.
- LLM 설정이 없어도 규칙 기반 fallback으로 파이프라인은 진행되어야 합니다.

서비스 상태 확인:

```powershell
docker compose ps
```

마이그레이션 적용:

```powershell
docker compose run --rm db-migrate apply --seeds
```

agent-worker 설정 확인:

```powershell
docker compose exec -T agent-worker python -c "from app.core.config import get_settings; s=get_settings(); print('storage=', s.report_storage_backend); print('local_dir=', s.report_local_storage_dir); print('use_llm=', s.report_use_llm); print('llm_provider=', s.report_llm_provider); print('llm_model=', s.report_llm_model)"
```

로컬 저장소를 쓰는 경우 기대값:

```text
storage= local
local_dir= /workspace/data/report-storage
```

## 3. 테스트 종목 확인

예시는 삼성전자 `005930` 기준입니다. 다른 종목을 쓰면 이후 명령의 `stock_id`, `stock_code`, 날짜 범위를 바꿉니다.

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select id, ticker, name from stocks where ticker='005930';"
```

## 4. Report 수집 작업 등록

네이버 금융 리서치 목록에 실제 리포트가 있는 날짜 범위를 사용합니다. `collected_reports=0`이면 날짜 범위 또는 `max_pages`를 조정합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/tasks/collect_report/enqueue" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","date_start":"2026-06-17","date_end":"2026-06-24","max_pages":2},"dedupe":false}'
```

큐 등록 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select id, task_type, status, stock_id, task_context from processing_queue where task_type='collect_report' order by id desc limit 5;"
```

## 5. Queue 실행 순서

각 단계는 `run_count`와 `results`를 확인합니다. `run_count=0`이면 해당 task type의 pending 작업이 없는 상태입니다.

### 5.1 수집

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/collect_report/run-batch" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_runs":5}'
```

수집 결과 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select rd.id, rd.ticker, rd.title, rd.published_at, rrd.securities_firm, rrd.pdf_url, rrd.has_pdf, rrd.parsing_status from raw_documents rd join report_raw_details rrd on rrd.raw_document_id = rd.id where rd.source_type='REPORT' order by rd.id desc limit 10;"
```

### 5.2 PDF 저장과 파싱

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/process_report/run-batch" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'
```

로컬 파일 저장 확인:

```powershell
Get-ChildItem -Recurse data\report-storage | Select-Object FullName,Length
```

파싱 결과 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select rd.id, rd.ticker, rrd.securities_firm, left(rd.title, 60) as title, rrd.has_pdf, rrd.s3_key, rrd.parsing_status, rrd.target_price, rrd.investment_opinion, left(rrd.key_rationale, 120) as rationale from raw_documents rd join report_raw_details rrd on rrd.raw_document_id = rd.id where rd.source_type='REPORT' order by rd.id desc limit 10;"
```

기대 기준:

- `has_pdf = t`
- `parsing_status = success`
- `s3_key`가 `reports/{stock_code}/...pdf` 형태
- `target_price`, `investment_opinion`, `key_rationale` 중 일부가 채워짐

밸류에이션 fact 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select raw_document_id, stock_id, ticker, broker, target_price, forward_eps_est, methodology, applied_multiple, implied_multiple, peer_group, extraction_source, needs_review from report_valuation_facts order by id desc limit 10;"
```

### 5.3 Canonical 정규화

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/normalize_report/run-batch" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'
```

정규화 결과 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select sd.id as source_document_id, se.id as event_id, se.stock_id, se.source_type, se.event_type, se.signal_direction, se.needs_review, left(se.title, 80) as title from source_documents sd join signal_events se on se.source_document_id = sd.id where sd.source_type='REPORT' order by se.id desc limit 10;"
```

지표 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select sm.signal_event_id, sm.metric_name, sm.metric_value, sm.metric_unit from signal_metrics sm join signal_events se on se.id = sm.signal_event_id where se.source_type='REPORT' order by sm.id desc limit 20;"
```

### 5.4 Deterministic Report 분석

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/analyze_report/run-batch" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'
```

분석 결과 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select ar.id as analysis_result_id, ar.stock_id, ar.run_key, ar.analysis_mode, ar.base_score, ar.warning, ag.id as agent_result_id, ag.method_signal, ag.method_score, ag.method_detail->>'source' as source, ag.method_detail->'report_quant'->'valuation' as valuation from analysis_results ar join agent_results ag on ag.analysis_result_id = ar.id where ag.method_detail->>'source' = 'REPORT' order by ar.id desc limit 10;"
```

기대 기준:

- `source = REPORT`
- `analysis_results`, `agent_results` row가 생성됨
- `method_detail.report_quant.valuation`에 구조화 payload가 들어감
- 후속 `aggregate_signal` 작업이 등록됨

## 6. Aggregator 실행

`analyze_report`는 Report `analysis_results`를 만든 뒤 `aggregate_signal`을 직접 등록합니다. Report 런타임은 Aggregator로 직접 이어집니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/aggregate_signal/run-batch" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN} `
  -ContentType "application/json" `
  -Body '{"max_runs":10}'
```

최종 결과 확인:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select fs.id, fs.stock_id, fs.signal_date, fs.signal, fs.final_score, fs.source_agreement, fs.warning_level, fs.score_breakdown->'REPORT' as report_breakdown from final_signals fs where fs.stock_id=1 and fs.run_key='AGGREGATED' order by fs.id desc limit 5;"
```

기대 기준:

- `score_breakdown`에 `REPORT` 키가 있음
- Report가 있으면 `score_breakdown.REPORT.data_status`가 `ok` 또는 `partial`
- `score_breakdown.REPORT.valuation`이 존재함
- Report는 근거 소스이며 현재 `final_score` 산정에는 직접 반영하지 않음

## 7. Queue 상태 점검

전체 Report 관련 queue 상태:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select task_type, status, count(*) from processing_queue where task_type in ('collect_report','process_report','normalize_report','analyze_report','aggregate_signal') group by task_type, status order by task_type, status;"
```

최근 실패 작업:

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select id, task_type, stock_id, status, retry_count, last_error, task_context from processing_queue where status='failed' order by updated_at desc limit 10;"
```

Report collector run 집계:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8011/internal/stats/collectors/runs?collector_type=REPORT&limit=10" `
  -Headers @{"X-Internal-Token" = $env:INTERNAL_API_TOKEN}
```

## 8. 문제 해결

### `collect_report` 결과가 0건인 경우

- 날짜 범위가 네이버 리서치 목록의 실제 작성일과 맞는지 확인합니다.
- `max_pages`를 늘립니다.
- 종목 코드가 `stocks.ticker`와 일치하는지 확인합니다.
- agent-worker 로그의 `report_collection_summary`에서 `collected_reports`, `saved_reports`, `invalid_date_reports`, `missing_pdf_reports`, `enqueued_reports`를 확인합니다.

크롤러 직접 확인:

```powershell
docker compose exec -T agent-worker python -c "from datetime import datetime; from app.collectors.report.crawler import collect_stock; reports=collect_stock('', '005930', max_pages=2, date_start=datetime(2026,6,17), date_end=datetime(2026,6,24,23,59,59)); print(len(reports)); [print(r.get('firm'), r.get('date'), r.get('title'), r.get('pdf_direct_url')) for r in reports]"
```

### PDF 저장은 됐지만 파싱이 실패한 경우

- `report_raw_details.parsing_status`와 `last_error` 또는 worker 로그를 확인합니다.
- `REPORT_STORAGE_BACKEND=local`이면 `data/report-storage`에 PDF가 있는지 확인합니다.
- PDF 텍스트 추출이 빈약한 증권사 양식이면 fallback parser fixture를 추가해 보정합니다.
- LLM 보강 오류가 있어도 규칙 기반 fallback 결과가 유지되어야 합니다.

### `analyze_report` 이후 최종 결과가 없는 경우

- `aggregate_signal` 작업이 pending 상태인지 확인합니다.
- `aggregate_signal` 입력의 `source_analysis_result_ids`에 Report `analysis_result_id`가 들어있는지 `processing_queue.task_context`를 확인합니다.
- 큐 드레인 데몬이 꺼져 있다면 `/internal/queue/run-cycle` 또는 `aggregate_signal/run-batch`를 실행합니다.
### `score_breakdown.REPORT`가 missing인 경우

- `agent_results.method_detail.source`가 `REPORT`인지 확인합니다.
- `aggregate_signal`이 Report `analysis_result_id`를 포함한 task로 실행됐는지 확인합니다.
- 같은 종목/날짜에서 여러 task가 순차 실행된 경우, 최신 `final_signals` row의 `score_breakdown`을 확인합니다.

## 9. 완료 기준

스모크 테스트는 아래 조건을 만족하면 통과로 봅니다.

- `collect_report`가 1건 이상 raw 문서를 저장하거나 기존 raw 문서를 재사용함
- `process_report`가 PDF 저장과 파싱 결과를 `report_raw_details`에 기록함
- `report_valuation_facts`에 구조화 fact가 저장됨
- `normalize_report`가 `source_documents`, `signal_events`, `signal_metrics`를 생성함
- `analyze_report`가 Report `analysis_results`, `agent_results`를 생성함
- 후속 `aggregate_signal` 작업이 등록됨
- `final_signals.score_breakdown.REPORT.valuation`에서 구조화된 밸류에이션 payload를 확인할 수 있음
- 사용자-facing 표현이 투자 행동 제안이 아니라 데이터 방향성, 근거, 소스 간 일치도, 추가 확인 필요 중심으로 유지됨

## 10. 관련 테스트

로컬 코드 변경 후 최소 검증:

```powershell
uv run pytest services\agent-worker\tests\test_report_task_handlers.py services\agent-worker\tests\test_report_e2e_pipeline.py services\agent-worker\tests\ml\test_report_aggregation_link.py services\agent-worker\tests\test_final_signal_aggregator.py -q
```

PDF 파서/LLM 보강을 수정했다면 추가:

```powershell
uv run pytest services\agent-worker\tests\test_report_parser_fallback.py services\agent-worker\tests\test_report_llm_wiring.py -q
```
