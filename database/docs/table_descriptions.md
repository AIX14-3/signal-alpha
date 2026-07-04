# 테이블 역할 설명서

전체 테이블을 Zone별로 묶어 **각 테이블이 무엇을 책임지는지**를 한 줄로 설명합니다.
ERD 다이어그램([`../erd/signal_alpha_core_erd.md`](../erd/signal_alpha_core_erd.md))이 *관계와 컬럼*을 보여준다면,
이 문서는 *테이블의 존재 이유(역할)* 를 봅니다.

> - 컬럼 전체 정의의 기준은 항상 `database/migrations/` SQL입니다.
> - 워커/에이전트가 **어떤 테이블을 쓰는지**(역할→테이블 방향)는 [`table_responsibility.md`](./table_responsibility.md)를 참고하세요. 이 문서는 그 반대인 **테이블→역할** 방향입니다.
> - **새 테이블을 추가하는 마이그레이션 PR은 이 문서에도 한 줄 설명을 반드시 추가해야 합니다** (`database/README.md` §4).

총 59개 테이블 (러너가 자동 관리하는 `schema_migrations` 원장 제외).

---

## Zone A — Market (002_market.sql)

종목 마스터와 시세·재무 등 시장 기준 데이터.

| 테이블 | 역할 |
| --- | --- |
| `stocks` | 분석 대상 종목 마스터. 티커·종목명·시장(KOSPI/KOSDAQ)·섹터를 보유. `is_target`로 수집 대상 on/off. 거의 모든 테이블이 `stock_id`로 참조하는 루트 테이블 |
| `ohlcv_data` | 일봉 시계열(시가/고가/저가/종가/거래량)과 외국인·기관 순매수. 장 마감 후 적재 |
| `fundamentals` | 종목별 재무 지표(매출·PER·PBR·ROE 등). 연간/분기 단위 |
| `price_snapshots` | 장중 현재가 스냅샷(시점별 누적 거래량 포함). 일봉과 달리 실시간 캡처 |
| `short_selling_trend` | 종목별 일별 공매도추이(공매도량·매매비중·거래대금·평균가). 키움 ka10014. 하락 압력 근거 |
| `credit_trade_trend` | 종목별 일별 신용매매동향(신규/상환/융자잔고/잔고율). 키움 ka10013. 반대매매 취약성 근거 |
| `securities_lending_trend` | 종목별 일별 대차거래추이(체결/상환/증감/대차잔고). 키움 ka20068. 공매도 선행지표 |

## Zone C — Collection 핵심 (003_collection_core.sql)

모든 종목 기반 수집의 공통 원본 계층. 수집 실행 로그 → 공통 원본 → source별 상세.

| 테이블 | 역할 |
| --- | --- |
| `collector_runs` | 모든 Collector 실행 1회당 1행. 수집 유형(DART/REPORT/HIRING/PATENT/DATALAB/PRICE)·실행 모드·상태를 기록하는 실행 원장 |
| `raw_documents` | 종목 기반 수집 원본의 공통 메타데이터. `source_hash`로 중복 방지, `(source_type, external_id)` 유니크. source별 detail 테이블의 부모 |
| `dart_raw_details` | DART 공시 원본 상세(접수번호·공시유형 등). `raw_documents`와 복합 FK로 1:1 |
| `report_raw_details` | 증권사 리포트 원본 상세(증권사·목표가·발행일·파싱 상태) |
| `hiring_raw_details` | 채용공고 원본 상세(키워드·공고 수·증감률). `stock_id` 필수 |
| `patent_raw_details` | 특허 원본 상세(출원번호·출원일·기술 분류). `llm_features`/`llm_status`로 LLM 보강(중요도) 캐시 보관 | 

## Zone C — DART 보조 (004_collection_dart.sql)

| 테이블 | 역할 |
| --- | --- |
| `dart_corp_codes` | DART 고유 기업코드(corp_code) ↔ 티커/종목 매핑 테이블. DART API 호출에 필요한 코드 변환 |
| `dart_collection_states` | 종목별 DART 증분 수집 상태(마지막 수집 구간 `last_bgn_de`/`last_end_de`). 다음 수집 시작점 추적 |

## Zone C — DataLab (005_collection_datalab.sql)

DataLab은 종목이 아닌 **카테고리 단위**로 수집하므로 자체 원본/상세 테이블을 사용한다.

| 테이블 | 역할 |
| --- | --- |
| `datalab_categories` | DataLab 검색 트렌드 수집 단위인 카테고리(섹터/테마) 마스터 |
| `datalab_category_stocks` | 카테고리 ↔ 종목 N:M 매핑. 카테고리 트렌드를 종목으로 해석하는 가중치(`weight`) 보유 |
| `datalab_category_keywords` | 카테고리에 속한 검색 키워드 목록(키워드 그룹 포함). `polarity`(demand/risk/neutral)로 검색량 방향성 태깅. `polarity_source`/`polarity_confidence`/`polarity_model`/`polarity_rationale`/`polarity_classified_at`(003)로 분류 출처(manual/llm/default; 기본값 `'default'` — 015)·신뢰도·모델·근거 기록 → 분석기가 `agent_results.llm_model`로 전파. `review_status`(approved/pending/rejected — 024)로 검색량 검증 관문 결과에 따른 라이프사이클 관리: approved=수집 대상(is_active=TRUE), pending=관리자 검수 대기(is_active=FALSE, 수집 제외). `validation_active_days`/`validation_window_days`/`validation_coverage`/`validated_at`로 검증 근거 기록 |
| `datalab_raw_documents` | DataLab 수집 원본의 공통 메타데이터(카테고리 기반). `raw_documents`의 DataLab판 |
| `datalab_raw_details` | 키워드·일자·세그먼트(기간/디바이스/성별/연령)별 검색지수 원본 상세. 급등 여부(`is_spike`) 포함 |

## Zone C — Hiring 기준선/확장 (006, 015, 016, 020, 021)

| 테이블 | 역할 | 마이그레이션 |
| --- | --- | --- |
| `hiring_baseline` | 종목별 채용 검색량 계절성 기준선(평균·분기 보정 계수). 채용 급증 판정의 비교 기준 | 006 |
| `hiring_signals` | HiringAnalyzer 분석 결과. 일자별 공고 수·기준선 대비 상대강도·급증 여부 저장. `calculation_phase`(A=14일MA/B=DataLab/C=기본값)로 분석 근거 추적 | 015, 021 |
| `hiring_sources` | 기업별 공식 채용 사이트 크롤러 설정(크롤러 유형/클래스/URL). `(stock_id, crawler_type)` 단위, 기업 추가/변경의 Single Source of Truth | 016 |
| `hiring_job_functions` | 직무(job function) 표준 분류 마스터(ENGINEER/SALES 등). 섹터 전반 직무 수요 전파의 기준 | 020 |
| `hiring_job_function_stocks` | 종목 ↔ 직무 노출 가중치 N:M. peer 직무 수요를 종목으로 전파(own-momentum 보완) | 020 |
| `hiring_quarantine` | 3c 게이트/`_insert_one` 오류로 `failed` 거부된 크롤 레코드의 격리 보관 + replay 원장. `record_payload`(parse된 dict, replay-data용)·`raw_payload`(원본 HTML/JSON, 하이브리드 크롤러만 채움, replay-reparse용)·`replayed_at`/`replayed_run_id`. 부분손상 backfill(전면개편 0건은 Phase 5 소관) | 013, Phase 4 |

## Zone D — Processing (007_processing.sql)

raw 계층을 정규화 계층으로 변환하는 처리 영역.

| 테이블 | 역할 |
| --- | --- |
| `processing_queue` | 정규화 작업 큐. Collector가 등록하고 Normalizer가 소비. `stock_id` nullable(DataLab은 카테고리 단위), `source_raw_ids` 배열로 처리 대상 추적 |
| `source_documents` | 정규화된 출처 문서. 원본(`raw_documents`)에 신뢰도(`reliability_level`)·공식 여부(`is_official`)를 부여한 분석용 계층 |
| `signal_events` | 정규화된 시그널 이벤트(이벤트 유형·방향·임팩트). Agent 분석의 기본 입력 단위. `event_hash`로 중복 방지 |
| `signal_metrics` | 시그널 이벤트에 딸린 정량 지표(name-value). 수치 데이터의 DB 고정값 |
| `validation_logs` | 정규화/분석 단계의 검증 결과 로그. 배열 FK를 강제 못하는 source trace를 검증·기록 |
| `dead_letter` | 종착 실패(재시도 소진/timeout) `processing_queue` 태스크의 격리 아카이브 + replay 원장. `processing_queue_id` 유니크로 멱등 아카이브, `replayed_at`/`replayed_task_id`로 재등록 이력 (005_dead_letter.sql, Phase 2 DLQ) |

## Zone B — User 기본 (008_users_billing_base.sql)

| 테이블 | 역할 |
| --- | --- |
| `users` | 서비스 회원 마스터(회원코드·이메일·리스크 동의·soft delete) |
| `subscription_plans` | 구독 요금제 정의(free/pro/premium, 관심종목 한도 등) |

## Zone E — Analysis (009_analysis.sql)

Agent·ML의 분석 결과와 최종 시그널.

| 테이블 | 역할 |
| --- | --- |
| `analysis_requests` | 분석 요청 단위(사용자 요청 또는 배치). 종목·상태·분석 모드(full/dart_only/quick) |
| `analysis_results` | 분석 1건의 대표 결과. 기준 점수(`base_score`)·법적 고지(`disclaimer`)·근거 시그널 ID 배열 보유. `(stock_id, analysis_date, mode, run_key, version)` 유니크 |
| `quant_scores` | 분석 결과의 퀀트 점수 상세(점수 분해·소스 일치도). `analysis_results`와 1:1 |
| `ta_scores` | 분석 결과의 기술적 분석(TA) 점수. 1:1 |
| `ai_scores` | 분석 결과의 AI/대체데이터 점수(DART·리포트·대체데이터). 1:1 |
| `agent_results` | 분석 결과의 토론 방식(D-1~D-5)별 결과(방식 점수·상세 JSON). `(result_id, debate_method)` 유니크 |
| `xgb_model_versions` | XGBoost 보정 모델 버전 레지스트리. `is_active` 부분 유니크로 활성 모델 1개 보장 |
| `ml_scores` | 분석 결과에 대한 ML 보정 점수(`calibrated_score`). 모델 버전 참조 |
| `final_signals` | 사용자/프론트에 노출되는 최종 시그널. `final_score`·신호·게시 여부·법적 고지 보유. `is_current` 부분 유니크+트리거로 조합당 현재 시그널 1건. `consensus_score`·`positive_evidence`/`caution_evidence`로 Alternative consensus 출력 |
| `score_history` | 최종 점수 변동 이력. 시그널/분석 결과별 점수 추적 |
| `backtest_results` | 최종 시그널의 사후 성과(5일 변화율·적중 여부) 백테스트 |

## Zone E — Agent 임베딩/메모리 (20260701_1218_agent_embeddings_pgvector.sql)

7-에이전트화 Stage 0 임베딩 인프라(pgvector, 768차원). RAG 검색과 에피소드 메모리가 같은 차원을 공용.

| 테이블 | 역할 |
| --- | --- |
| `report_chunks` | 증권사 리포트 본문을 청크로 나눠 임베딩(`vector(768)`) 저장하는 RAG 검색용 테이블. `report_raw_details`(PK `raw_document_id`) 파생이라 원본 삭제 시 동반 삭제. `(report_raw_detail_id, chunk_index)` 유니크, HNSW 코사인 인덱스. 종목 필터를 exact 스캔으로 만들기 위한 `stock_id`(→`stocks`) 비정규화 + btree 인덱스(RAG 리콜 정확 보장) |
| `signal_episodes` | 종목·일자·`run_key` 단위 시그널 발화 1건의 에피소드 메모리. 발화 소스/방향/점수 요약(`sources` JSONB)과 임베딩을 보관하고 성패(`outcome` JSONB)는 사후 기록(NULL 시작). `(stock_id, signal_date, run_key)` 유니크, HNSW 코사인 인덱스 |

## Zone F — User 확장 (010_users_billing_extend.sql)

| 테이블 | 역할 |
| --- | --- |
| `signal_subscriptions` | 사용자 구독 상태. 활성 구독 1건만 허용(부분 유니크) |
| `watchlists` | 사용자 관심종목. `(user_id, stock_id)` 유니크 |
| `signal_journals` | 사용자가 시그널에 남긴 복기 메모(`user_memo`)·판단(`user_view`)·태그 + 작성 시점 신호 스냅샷. 구독 전용 기능 |
| `signal_journal_outcomes` | 저널 작성 후 실제 주가 변동 확정 결과(거래일 기준 7td/30td). 워커 outcome 러너가 기록, `(journal_id, horizon)` 유니크, 저널 삭제 시 CASCADE |
| `signal_journal_chart_prices` | 저널 차트용 종가 시리즈(종목×거래일). 워커 러너가 저널 있는 종목만 작성일 30일 전~최신 구간을 동기화 |
| `user_signal_reads` | 사용자별 시그널 읽음 표시. `(user_id, final_signal_id)` 유니크 |
| `user_sessions` | 사용자 refresh token 세션. refresh token hash, 만료, 폐기 시각 관리 |
| `social_accounts` | 소셜 로그인 연동(provider·provider_user_id) |
| `portone_verifications` | PortOne 본인인증 기록(imp_uid 등) |
| `terms_agreements` | 약관 동의 이력(약관 유형·버전별). `(user_id, terms_type, version)` 유니크 |

## Zone H — 지정학 리스크 Kill-Switch (20260703_2035_guard_kill_switch.sql, target=backend)

전쟁·휴전 등 지정학 충격 구간에 리포트 "노출"을 일시 차단하는 fail-safe 스위치. 발행 파이프라인은 건드리지 않고(노출 차단만), 관리자 수동 토글(안전 핵심 경로)이 워커 없이 동작해야 하므로 backend 소유. 워커 guard 데몬은 BACKEND_DATABASE_URL 로 이력·제안을 기록한다.

| 테이블 | 역할 |
| --- | --- |
| `guard_site_status` | 차단 상태 싱글턴 1행(단일 진실원천). `status`(ok/blocked)·`scope`(report_generation/report_view/whole_site)·`mode`(manual/advisory/auto)·`reason`·`resume_at`. 공개 `GET /api/guard/status` 와 프론트 게이트가 읽는다 |
| `guard_news_events` | GDELT 수집·LLM 판정한 뉴스 이력. `article_hash` 유니크(중복 제거), severity/direction/summary/regions 등 판정 결과 + `prompt_version` |
| `guard_recommendations` | advisory 모드 차단 제안(pending/approved/rejected). 관리자 승인 시 `guard_site_status` 를 blocked 로 전환 |
| `guard_status_audit` | 상태 변경 감사 로그(관리자·에이전트 공통 actor). action/scope/reason/actor |

## Zone G — Admin (011_admin.sql)

| 테이블 | 역할 |
| --- | --- |
| `admin_accounts` | 관리자 계정(이메일·비밀번호 해시·활성 여부) |
| `admin_sessions` | 관리자 세션 토큰과 만료 시각 |

레거시 `report_raw` / `report_signal`(구 report RAG MVP)은
`20260630_1200_drop_legacy_report_raw_signal.sql`로 DROP 됐다. Report 런타임은 공용 경로
(`raw_documents` → `report_raw_details` 이후 `source_documents`/`signal_events`/`signal_metrics`
+ 분석 테이블)만 사용한다. (`report_chunks`는 RAG 복구 후보 스키마이며 현재 런타임 저장 경로가 아니다.)

## 시스템 테이블

- `schema_migrations(filename PK, checksum, applied_at)` — `database/migrate.py`가 자동 생성·관리하는 적용 원장. 마이그레이션 파일로 만들지 않으며 이 표에도 포함하지 않는다.

## 트리거 (012_triggers.sql)

테이블은 아니지만 참고: `updated_at` 자동 갱신 트리거 일괄 부착 + `final_signals.is_current` 단일화 트리거(`set_final_signal_current`) 등 2종 함수.
