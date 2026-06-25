# DART 데이터 레이어 L1~L6 전체 재설계 + 50/neutral 고정 버그 제거 계획

> 영역: agent-worker. 앱 루트: `services/agent-worker/app`.
> 목표: `final_score=50/neutral/CAUTION` 고정 버그 제거 + L1~L6 결정론 신호 파이프라인 완성.

## 배경 (왜 하는가)
DART 분석 결과가 모든 종목/공시에서 `final_score=50.00 / signal=neutral / confidence=50.00 /
warning_level=CAUTION`로 고정된다(실측: 005930 발행 43건 전부 동일). 설계상 L1~L6 레벨별 정량
신호가 차등 점수를 만들어야 하는데, 실제 점수 엔진은 방향 없는 룰만 돌고 레벨 신호가 점수
파이프라인에 합류하지 않는다.

근본 원인(코드·실측 확정):
1. `app/analyzers/dart/rules.py` `classify_dart_report`가 `signal_direction`을
   neutral/mixed/unknown만 반환(positive/negative 절대 안 냄) → `app/analyzers/dart/source_result.py`
   `_score_delta` 항상 0 → `app/orchestrator/dart/tasks.py` `_to_db_score=(0+1)*50=50`.
2. L1/L2/L3 신호가 점수에 미합류: `dart_financial_facts`·`dart_ownership_events`·
   `dart_employee_stats`는 수집기·sync·리포지토리·마이그레이션(006/011/013)까지 **이미 존재하나
   큐/핸들러 미배선**이고, 어떤 analyzer도 안 읽음. 분석 입력은 L1 공시 `signal_events`뿐.
3. 단일 소스(DART만) 집계 → `app/orchestrator/aggregation/tasks.py`에서 source_agreement=LOW /
   consensus_score=50(`len(available)==1`) / warning_level=CAUTION 강제.

방침(확정): **전체 L1~L6 풀 재설계**. 점수/방향은 **결정론**(정량·룰)으로 산출하고
**per-source 생성형 LLM 판정 코드는 제거**(보존하지 않음). 단 **파싱이 필요한 곳(L5 발주처/엔티티
추출)은 LLM 파싱 허용**. 끝단 SYNTHESIZE만 생성형 LLM 유지.
기대 결과: 차등 final_score, 다소스 집계로 CAUTION/50 탈출, L6 백테스트 lift로 신호 채택 검증.

## 점수 합류 메커니즘 (검증 완료 — 이 설계의 토대)
- **fan-in**: `packages/data-access/.../repositories/analysis.py` `list_latest_source_results_for_stock`
  가 `run_key LIKE 'DART%' OR 'PRICE%' OR 'REPORT%' OR 'HIRING%' OR 'PATENT%' OR 'DATALAB%'`로 모은다.
- **결정**: source_type을 새로 만들지 않고 **DART 내부 다신호**로 간다. 각 레벨을 자기 run_key
  (`DART_FIN`/`DART_OWN`/`DART_EMP`, 기존 공시는 `DART`)로 독립 analysis_result+agent_result 적재 →
  모두 `LIKE 'DART%'`라 자동 fan-in. `method_detail.source`에 fine 라벨(DART_FINANCIAL 등),
  `aggregation/tasks.py`의 `SOURCE_ALIASES`가 `DART_*`→coarse `DART`로 접는다
  (`SCORING_SOURCES={"DART","ALTERNATIVE"}` 불변).
- **DART 패밀리 결합**: 기존 `_coalesce_alternatives`/`_blend_alternative`(ALTERNATIVE 트리오 접기)
  패턴을 그대로 미러링한 `_coalesce_dart`/`_blend_dart`/`_nest_dart`를 추가 → 4개 DART 신호가 PRICE를
  4:1로 압도하지 않게 1개 DART peer로 가중평균, 레벨별 근거는 breakdown 하위 카드로 보존.
- **분석기 템플릿**: `app/orchestrator/price/tasks.py`가 signal_events 없이 per-stock
  agent_result를 `run_key=PRICE`로 emit하는 패턴이 L1~L3 분석기의 본보기.

## 스펙↔코드 불일치 (스펙만 믿지 말 것)
- L4 `report_chunks`+pgvector는 `001_baseline.sql`에서 **의도적 제거됨**. 스펙의 "ALTER TABLE
  report_chunks"는 outdated → **신규 테이블+pgvector 재도입 필요(팀 합의 항목)**.
- `langchain-text-splitters`는 이미 `services/agent-worker/pyproject.toml`에 선언됨 → 스펙의
  "미선언 정리" 선결과제는 해소됨.

## 제약 (반드시 준수)
- 마이그레이션: `python database/migrate.py new "..."` → **타임스탬프 파일명**(YYYYMMDD_HHMM_*.sql).
  정수 순번(NNN_) 금지. **SQL은 LF 필수**(.gitattributes/checksum). Alembic 재도입 금지.
- 런타임 DB asyncpg raw, PG16. `agent_results.debate_method`는 D-1~D-5만(레벨별 별도
  analysis_result라 D-1 재사용 가능).
- 기존 수집기/sync/리포/마이그레이션(006/011/013) **재사용**, 신규 생성 금지.

## Phase별 실행 순서
권장 1차 마일스톤: **Phase 0 → 1 → 2 → 5(부분)** 으로 e2e에서 "50 탈출" 입증 후 나머지.

### Phase 0 — rules.py 핫픽스 (즉시, 단독 PR) ★최우선
`app/analyzers/dart/rules.py classify_dart_report`에 결정론 방향 매핑 추가:
- positive: 공급계약체결/단일판매계약, 자기주식취득, 무상증자, 흑자전환, 현금·현물배당.
- negative: 유상증자결정(희석)/전환사채발행, 감자, 관리종목, 상장폐지, 감사의견 거절·한정,
  횡령·배임, 영업정지, 적자전환, 부도.
- 정정/주요사항/정기보고서는 현행 유지. `source_result.py _score_delta`는 이미 positive/negative
  처리하므로 변경 불필요. 테스트(`services/agent-worker/tests/` dart rules)에 호재→positive·
  악재→negative·score≠0 케이스 추가. **DoD**: 공급계약 공시 → direction=positive, _to_db_score>50.

### Phase 1 — per-source 생성형 LLM 판정 제거 + 파싱 클라이언트 분리·보존
- 제거: `app/agents/dart/agent.py`의 LLM 분기→룰 단일 경로, `graph.py`의 llm_analyzer 인자,
  `orchestrator/dart/tasks.py DartAnalyzeTaskHandler`의 llm_analyzer/llm_high_impact_only,
  `orchestrator/queue/handlers.py`의 해당 인자(이미 None).
- 보존·이동: `app/analyzers/dart/llm.py`의 **HTTP 클라이언트/JSON 파싱 헬퍼**(LlmClient Protocol,
  GeminiGenerateContentClient, OpenAiChatClient, JSON 로더)는 신규 `app/llm/clients.py`로 추출해
  L5 파싱이 import. **판정부**(DartLlmAnalyzer.analyze, should_use_dart_llm, _build_prompt)는 삭제.
  SYNTHESIZE(`app/synthesis/tasks.py`)가 쓰는 클라이언트와 중복 시 그쪽으로 통합. **DoD**: 룰만
  동작, 생성형 LLM 호출 0, 파싱 클라이언트 import 가능.

### Phase 2 — L1 정형재무 분석기 + 신호 emit + 배선 (인프라 기존, 분석기·배선 신규)
- 신규 `app/analyzers/dart/financial_metrics.py`: ★4 파생지표 순수함수(입력=`DartFinancialFactsRepository.list_by_corp_year`
  다년치 + `collectors/dart/account_mapping.py`). revenue/operating_income YoY·QoQ(누적 차분),
  debt_ratio, inventory/receivable turnover, interest_coverage, capex_trend(기울기), earnings_quality
  (OCF−NI). 분모0/음수 가드. magnitude=z-score.
- 신규 `financial_signal.py`: 파생지표 → SourceAgentOutput(signed [-1,+1]) + signal_events dict.
- 신규 `app/orchestrator/dart/financials_tasks.py`: Collect(기존 `DartFinancialsSyncService`)/
  Normalize(facts→파생→`upsert_source_document`(rcept_no)+`upsert_signal_event`(source_type='DART',
  event_type='financials')+`upsert_signal_metric`)/Analyze(PRICE 패턴 미러:
  `upsert_analysis_result`(run_key='DART_FIN')+`upsert_agent_result`(D-1, method_detail.source='DART_FINANCIAL')).
- 배선: `task_types.py`에 COLLECT/NORMALIZE/ANALYZE_DART_FIN, `handlers.py` 등록,
  `orchestrator/dart/scheduler.py` enqueue, `api/routes/dart.py /e2e/run`에 단계 추가.
- **DoD**: 005930 L1 적재 후 DART_FIN agent_result 차등 score, signal_events(financials) 생성.

### Phase 3 — L2 지분·내부자 분석기 + 배선 (L1과 동형)
신규 `ownership_signal.py`(holder_type별 순매수(+)/순매도(−), magnitude=ratio_delta z-score,
evidence=rcept_no) + `orchestrator/dart/ownership_tasks.py`(run_key='DART_OWN') + task_types/handlers/
scheduler/e2e 배선. **DoD**: 순매수→positive, 순매도→negative.

### Phase 4 — L3 임직원 분석기 + 배선 (동형)
신규 `employee_signal.py`(전사합계 headcount YoY, 동일 reprt 비교) + `orchestrator/dart/employee_tasks.py`
(run_key='DART_EMP') + 배선. **DoD**: headcount 증가→positive.

### Phase 5 — 집계 합류 (DART 패밀리 결합) [L1~L3 후]
`app/orchestrator/aggregation/tasks.py`에 `_coalesce_dart`/`_blend_dart`/`_nest_dart` 추가
(`_coalesce_alternatives` 미러), `__call__`에서 ALTERNATIVE 접기 다음 DART 접기 배선. 레벨 ANALYZE는
`aggregate_ctx.signal_date`를 analysis_date로 사용(PRICE 동일, fan-in 날짜 정렬). 회귀 테스트
`tests/test_aggregation_*.py`. **DoD**: 멀티 DART 신호 시 final_score 차등, available≥2, consensus≠50,
warning_level NORMAL 가능.

### Phase 6 — L4 비정형 RAG 토대 [pgvector 합의 후]
마이그레이션(`migrate.py new "report_chunks_l4_rag"`): `CREATE EXTENSION IF NOT EXISTS vector` +
신규 report_chunks(stock_id,rcept_no,source,section_type,chunk_index,content,embedding VECTOR(1024),
ivfflat 인덱스). 리포 `report_chunks.py`. 섹션 분해기(사업의내용/MD&A/위험요인/주석/감사) + 기존
`chunker.py` + BGE-M3 임베딩. L4는 retrieval 토대(신호 emit 아님). **미승인 시** 텍스트 섹션만 적재
(임베딩 보류) 폴백. **DoD**: 본문→섹션→청킹→임베딩→top-k 검색 동작.

### Phase 7 — L5 엔티티·관계 + 파싱 LLM [L4 의존]
마이그레이션: entities(uq canonical_name) + entity_relations(src/dst/relation_type/evidence_rcept/
confidence/observed_at). 리포 `entities.py`. 신규 `entity_extraction.py`: 수주/투자 공시 본문 →
**Phase1 보존 `app/llm/clients.py`로 파싱 LLM** 발주처/거래처 추출 → 정규화(corp_code 우선 매칭) →
upsert. **DoD**: 공급계약 공시에서 발주처 엔티티·관계 적재, confidence 보존.

### Phase 8 — L6 백테스트 채택 게이트 [signal_events 누적 후]
마이그레이션: event_study_panel(signal_event_id FK, fwd_return_1d/5d/20d, abnormal_return_20d,
benchmark, uq signal_event_id). 리포 `event_study.py`. 신규 `app/backtest/event_study.py`:
signal_events×ohlcv_data forward return. **look-ahead 차단**: `MarketDataRepository.get_price_on_or_after(event_date+1)`
부터 forward window. abnormal = 종목 − 벤치(kospi20, 마이그레이션 023) 동일창. IC/hit rate/decay.
**사전 임계치 고정** 채택 게이트(사후조정 금지). L6은 emit 아님(채택 좌우). **DoD**: 신호유형별
lift 리포트, look-ahead 0, 채택/기각 판정.

## 손대지 않는 것
- 기존 `analyzers/dart/financials.py`(텍스트 정규식)는 L1 파생이 백테스트 우위 증명 전까지 유지(점진 전환).
- 끝단 SYNTHESIZE(생성형 LLM)·RISK_VETO 불변. ALTERNATIVE/PRICE/REPORT 집계 경로 불변(DART 결합만 추가).

## 검증 (end-to-end)
- 단위: Phase0 호재/악재 방향·score≠0 / Phase2-4 파생지표 경계값(분모0/음수/결측) / Phase5
  `_coalesce_dart` 결합·nest 회귀 / Phase8 look-ahead 차단·IC·abnormal.
- 마이그·스키마: `uv run python database/migrate.py apply` + `database/tools/check_schema.py`(drift 0).
- 테스트: `cd services/agent-worker && uv run pytest tests/`, `cd packages/data-access && uv run pytest tests/`.
- 로컬 e2e: 워커 기동 후 `POST /internal/dart/e2e/run` { stock_id, stock_code:"005930",
  bgn_de:"2024-01-01", end_de:"2024-12-31", force_reprocess:true, run_until_idle:true } →
  응답 `final_signals.items[].final_score`가 **50.00 아닌 차등값**, signal/confidence/warning_level
  다양화, `score_breakdown.DART` 하위에 financial/ownership/employee 카드 nest 확인.
- L6: event_study_panel 적재 후 신호유형별 평균 abnormal_return·hit rate + 채택 게이트 통과/기각.

## 위험 · 미결정
- **표준계정 매핑 사전**(account_mapping.py 11종 임시): Phase2 착수 전 매핑 범위 확정, account_nm
  폴백, 미매핑은 신호 제외+로깅.
- **pgvector 재도입(L4)**: 의도적 제거됨 → PG16 운영 영향, 팀 합의 필수(미승인 시 임베딩 보류 폴백).
- **엔티티 정규화(L5)** 난이도 高: confidence 보존, corp_code 우선 매칭, 파싱 LLM은 추출만.
- **백테스트 look-ahead/생존편향/휴장·상폐** 가드. **분기 누적 단일분기화**(반기/3Q 차분, fiscal_period 보존).
