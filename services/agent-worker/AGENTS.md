# AGENTS.md

이 디렉터리는 내부 워커 서비스입니다. 데이터 수집, 정규화, 큐 실행, 분석, LLM/RAG 작업, 키움 가격 수집 데몬을 담당합니다.

## 경계

- 사용자-facing API 책임은 `services/main-server`에 둡니다.
- UI 책임은 `web`에 둡니다.
- SQL을 중복 작성하지 말고 가능한 경우 `packages/data-access` repository를 사용합니다.
- 워커는 내부 개발용 엔드포인트를 노출할 수 있지만, 이를 공개 제품 API로 취급하면 안 됩니다.

## 큐와 오케스트레이션

현재 구현된 DART task type은 다음뿐입니다.

- `collect_dart`
- `normalize_dart`
- `analyze_dart`

기획 문서에 언급되어 있어도 `analyze_report`, `analyze_alt`, `aggregate_debate`, `ml_score`, `create_final_signal` 핸들러가 존재한다고 가정하지 마세요. 새 핸들러는 명시적으로 추가하고 `app/orchestrator/queue` 경로에서 테스트하세요.

## Collector 규칙

Collector가 해야 하는 일:

- 외부 API 또는 crawler 호출
- 원천 데이터 저장
- 소스별 detail row 저장
- 필요한 경우 후속 처리 큐 등록
- `collector_runs` 기록

Collector가 하면 안 되는 일:

- LLM 호출
- 투자 판단
- 최종 사용자-facing signal 직접 저장

특수 저장 흐름:

- DART: `raw_documents -> dart_raw_details -> processing_queue`
- Report: canonical 경로는 `raw_documents -> report_raw_details -> report_chunks`입니다. legacy `report_raw`/`report_signal`에 신규 의존성을 만들지 마세요.
- Hiring: `raw_documents -> hiring_raw_details -> processing_queue`
- Patent: `raw_documents -> patent_raw_details -> processing_queue`
- DataLab: 카테고리 기반 `datalab_raw_documents -> datalab_raw_details -> processing_queue(stock_id=NULL)`
- Price: 데몬이 `price_snapshots`, `ohlcv_data`에 저장합니다. `raw_documents`나 `processing_queue`를 사용하지 않습니다.

## Analyzer 규칙

Analyzer는 DB에 저장된 source 데이터를 읽고 구조화된 `SourceResult` 계열 출력을 반환해야 합니다. Analyzer가 직접 scraping하거나 원천 수집 API를 호출하면 안 됩니다.

- DART analyzer는 결정적 rule과 고임팩트 공시용 선택적 LLM을 사용할 수 있습니다.
- PRICE analyzer는 `ohlcv_data`를 읽습니다. 키움 API를 직접 호출하면 안 됩니다.
- Report analyzer는 일부 legacy 상태입니다. 변경 시 canonical table과 정규화 출력(`source_documents`, `signal_events`, `signal_metrics`, `analysis_results`, `agent_results`)을 우선하세요.

## LLM/RAG 안전 규칙

- LLM이 수치 값을 만들어내게 하지 마세요.
- LLM JSON은 사용 전에 검증하세요.
- timeout, 잘못된 JSON, 금지 표현에 대한 fallback 경로를 추가하세요.
- 프롬프트는 매수/매도/보유 추천과 목표 수익률 표현을 금지해야 합니다.
- Report PDF 원문 텍스트를 사용자-facing 콘텐츠로 노출하지 마세요.

## Price Collector

키움 REST 가격 수집기는 `agent-worker` 내부 lifespan 백그라운드 task로 실행되며 `PRICE_COLLECTOR_ENABLED`로 제어합니다.

- 수집 대상 종목은 반드시 `stocks.is_target`에서 가져옵니다.
- 데몬 중복 polling을 막기 위해 advisory lock을 사용합니다.
- 120영업일 backfill이 끝나기 전까지 `insufficient_history`는 정상적인 analyzer 상태입니다.

## 테스트

이 디렉터리에서 실행합니다.

```powershell
uv run pytest
```

큐, collector, analyzer를 변경했다면 `services/agent-worker/tests` 아래에 집중된 테스트를 추가하거나 갱신하세요.
