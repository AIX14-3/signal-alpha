# Report PDF Gemini 파싱 보강 로컬 테스트 가이드

이 문서는 Report PDF 파싱 보강 단계에서 Gemini LLM 호출이 실제로 연결되는지 로컬 Docker 환경에서 확인하는 절차입니다.

Signal Alpha는 투자 추천 서비스가 아닙니다. 이 테스트는 증권사 리포트 원문에서 목표주가, 원문 의견, 근거 후보를 구조화해 데이터 방향성과 근거 확인을 보조하는 경로만 검증합니다.

## 1. 전제

- Docker compose로 `postgres`, `agent-worker`가 실행 중이어야 합니다.
- DB migration과 seed가 적용되어 `stocks`에 테스트 종목이 있어야 합니다.
- 로컬 테스트에서는 PDF 저장소를 local backend로 둡니다.
- Gemini 호출을 위해 유효한 `GEMINI_API_KEY`가 필요합니다.

## 2. 환경 변수

프로젝트 루트 `.env`에 아래 값을 설정합니다.

```env
REPORT_STORAGE_BACKEND=local
REPORT_LOCAL_STORAGE_DIR=data/report-storage

REPORT_USE_LLM=true
REPORT_LLM_PROVIDER=gemini
REPORT_LLM_MODEL=gemini-2.0-flash
REPORT_LLM_TIMEOUT_SECONDS=20

GEMINI_API_KEY=your-gemini-api-key
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

Docker compose에서 `agent-worker`는 `REPORT_LOCAL_STORAGE_DIR`을 `/workspace/data/report-storage`로 넘깁니다. 루트의 `./data`가 컨테이너 `/workspace/data`에 마운트되어야 합니다.

환경 변경 후 `agent-worker`를 재생성합니다.

```powershell
docker compose up -d --no-build --force-recreate agent-worker
```

컨테이너가 설정을 읽는지 확인합니다.

```powershell
docker compose exec -T agent-worker python -c "from app.core.config import get_settings; s=get_settings(); print(s.report_storage_backend); print(s.report_use_llm); print(s.report_llm_provider); print(s.report_llm_model)"
```

기대값:

```text
local
True
gemini
gemini-2.0-flash
```

## 3. 테스트용 리포트 수집 작업 등록

먼저 테스트할 종목의 `stock_id`를 확인합니다.

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select id, ticker, name from stocks where ticker='005930';"
```

네이버 금융 리서치 목록에 실제 리포트가 있는 날짜 범위를 지정해 `collect_report`를 등록합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/tasks/collect_report/enqueue" `
  -ContentType "application/json" `
  -Body '{"stock_id":1,"priority":"batch","task_context":{"stock_code":"005930","date_start":"2026-06-17","date_end":"2026-06-24","max_pages":1},"dedupe":false}'
```

수집 작업을 실행합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/collect_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":5}'
```

`collected`가 0이면 날짜 범위에 리포트가 없거나 제목/날짜 필터에 맞는 항목이 없는 것입니다. 이 경우 네이버 목록의 실제 작성일에 맞춰 `date_start`, `date_end`를 조정합니다.

## 4. PDF 다운로드와 Gemini 보강 파싱 실행

`process_report`를 실행합니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/process_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":5}'
```

이 단계에서 수행되는 일:

- PDF URL 다운로드
- local report storage 저장
- PDF 전체 텍스트 추출
- 규칙 기반 fallback 파싱
- `REPORT_USE_LLM=true`이면 Gemini로 목표주가, 원문 의견, 근거 후보 보강
- `report_raw_details` 업데이트
- `normalize_report` 작업 등록

## 5. 결과 확인

PDF 파일이 로컬 저장소에 생겼는지 확인합니다.

```powershell
Get-ChildItem -Recurse data\report-storage | Select-Object FullName,Length
```

DB 저장 상태를 확인합니다.

```powershell
docker compose exec -T postgres psql -U signal_alpha -d signal_alpha -c "select rd.id, rrd.securities_firm, rd.title, rrd.has_pdf, rrd.s3_key, rrd.parsing_status, rrd.target_price, rrd.investment_opinion, left(rrd.key_rationale, 120) as rationale from raw_documents rd join report_raw_details rrd on rrd.raw_document_id = rd.id where rd.source_type='REPORT' order by rd.id desc limit 10;"
```

확인 기준:

- `has_pdf = t`
- `parsing_status = success`
- `s3_key`가 `reports/{stock_code}/{publish_date}_{firm_slug}_{source_hash8}.pdf` 형태
- `target_price`, `investment_opinion`, `key_rationale` 중 일부가 채워짐

Gemini 보강이 실패해도 규칙 기반 fallback 결과가 유지됩니다. 이 경우 로그에 `[LLM 보강 오류]`가 찍힐 수 있습니다.

## 6. 후속 큐 실행

PDF 파싱 이후 현재 canonical 경로와 deterministic 분석 결과 저장까지 확인하려면 아래 작업을 실행합니다. 현재 Report 런타임에는 `embed_report`/RAG 단계가 연결되어 있지 않습니다.

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/normalize_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":5}'

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8011/internal/queue/analyze_report/run-batch" `
  -ContentType "application/json" `
  -Body '{"max_runs":5}'
```

## 7. 문제 해결

### `collected`가 0인 경우

- 날짜 범위가 네이버 목록의 작성일과 맞는지 확인합니다.
- `max_pages`를 늘려 오래된 리포트까지 탐색합니다.
- `collect_stock()`을 컨테이너에서 직접 실행해 목록 파싱 결과를 확인합니다.

agent-worker 로그에서 `report_collection_summary`를 확인합니다.

- `collected_reports`: 크롤러가 찾은 후보 수
- `saved_reports`: DB에 저장 또는 갱신된 raw 문서 수
- `invalid_date_reports`: 날짜 파싱 실패로 저장하지 않은 수
- `missing_pdf_reports`: 후보에는 있으나 PDF URL이 없는 수
- `enqueued_reports`: `process_report`로 넘긴 수

```powershell
docker compose exec -T agent-worker python -c "from datetime import datetime; from app.collectors.report.crawler import collect_stock; reports=collect_stock('', '005930', max_pages=1, date_start=datetime(2026,6,17), date_end=datetime(2026,6,24,23,59,59)); print(len(reports)); [print(r.get('firm'), r.get('date'), r.get('title'), r.get('pdf_direct_url')) for r in reports]"
```

### Gemini 호출이 안 되는 경우

- `REPORT_USE_LLM=true`인지 확인합니다.
- `REPORT_LLM_PROVIDER=gemini`인지 확인합니다.
- `REPORT_LLM_MODEL`이 비어 있지 않은지 확인합니다.
- `GEMINI_API_KEY`가 컨테이너에 전달되는지 확인합니다.

```powershell
docker compose exec -T agent-worker python -c "from app.core.config import get_settings; s=get_settings(); print(bool(s.gemini_api_key)); print(s.report_use_llm); print(s.report_llm_provider); print(s.report_llm_model)"
```

### PDF는 저장되지만 파싱 결과가 빈약한 경우

- 규칙 기반 fallback이 지원하지 않는 증권사별 표기가 있을 수 있습니다.
- 실제 추출 텍스트에서 목표주가, 의견, 근거 주변 문장을 확인해 `test_report_parser_fallback.py`에 fixture를 추가한 뒤 파서를 보정합니다.
- LLM 보강이 켜져 있으면 Gemini 응답 JSON 형식 오류나 quota 오류가 로그에 남을 수 있습니다.

## 8. 관련 테스트

```powershell
uv run pytest services\agent-worker\tests\test_report_parser_fallback.py services\agent-worker\tests\test_report_llm_wiring.py -q
uv run pytest services\agent-worker\tests\test_report_e2e_pipeline.py services\agent-worker\tests\test_report_task_handlers.py -q
```
