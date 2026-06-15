# 테이블 역할 설명서

전체 테이블을 Zone별로 묶어 **각 테이블이 무엇을 책임지는지**를 한 줄로 설명합니다.
ERD 다이어그램([`../erd/signal_alpha_core_erd.md`](../erd/signal_alpha_core_erd.md))이 *관계와 컬럼*을 보여준다면,
이 문서는 *테이블의 존재 이유(역할)* 를 봅니다.

> - 컬럼 전체 정의의 기준은 항상 `database/migrations/` SQL입니다.
> - 워커/에이전트가 **어떤 테이블을 쓰는지**(역할→테이블 방향)는 [`table_responsibility.md`](./table_responsibility.md)를 참고하세요. 이 문서는 그 반대인 **테이블→역할** 방향입니다.
> - **새 테이블을 추가하는 마이그레이션 PR은 이 문서에도 한 줄 설명을 반드시 추가해야 합니다** (`database/README.md` §4).

총 50개 테이블 (러너가 자동 관리하는 `schema_migrations` 원장 제외).

---

## Zone A — Market (002_market.sql)

종목 마스터와 시세·재무 등 시장 기준 데이터.

| 테이블 | 역할 |
| --- | --- |
| `stocks` | 분석 대상 종목 마스터. 티커·종목명·시장(KOSPI/KOSDAQ)·섹터를 보유. `is_target`로 수집 대상 on/off. 거의 모든 테이블이 `stock_id`로 참조하는 루트 테이블 |
| `ohlcv_data` | 일봉 시계열(시가/고가/저가/종가/거래량)과 외국인·기관 순매수. 장 마감 후 적재 |
| `fundamentals` | 종목별 재무 지표(매출·PER·PBR·ROE 등). 연간/분기 단위 |
| `price_snapshots` | 장중 현재가 스냅샷(시점별 누적 거래량 포함). 일봉과 달리 실시간 캡처 |

## Zone C — Collection 핵심 (003_collection_core.sql)

모든 종목 기반 수집의 공통 원본 계층. 수집 실행 로그 → 공통 원본 → source별 상세.

| 테이블 | 역할 |
| --- | --- |
| `collector_runs` | 모든 Collector 실행 1회당 1행. 수집 유형(DART/REPORT/HIRING/PATENT/DATALAB/PRICE)·실행 모드·상태를 기록하는 실행 원장 |
| `raw_documents` | 종목 기반 수집 원본의 공통 메타데이터. `source_hash`로 중복 방지, `(source_type, external_id)` 유니크. source별 detail 테이블의 부모 |
| `dart_raw_details` | DART 공시 원본 상세(접수번호·공시유형 등). `raw_documents`와 복합 FK로 1:1 |
| `report_raw_details` | 증권사 리포트 원본 상세(증권사·목표가·발행일·파싱 상태) |
| `hiring_raw_details` | 채용공고 원본 상세(키워드·공고 수·증감률). `stock_id` 필수 |
| `patent_raw_details` | 특허 원본 상세(출원번호·출원일·기술 분류) |
| `report_chunks` | 리포트 PDF에서 추출한 텍스트 청크 + pgvector 임베딩(VECTOR 1024, ivfflat). Report RAG 검색의 대상 |

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
| `datalab_category_keywords` | 카테고리에 속한 검색 키워드 목록(키워드 그룹 포함) |
| `datalab_raw_documents` | DataLab 수집 원본의 공통 메타데이터(카테고리 기반). `raw_documents`의 DataLab판 |
| `datalab_raw_details` | 키워드·일자·세그먼트(기간/디바이스/성별/연령)별 검색지수 원본 상세. 급등 여부(`is_spike`) 포함 |

## Zone C — Hiring 기준선/확장 (006, 015, 016)

| 테이블 | 역할 | 마이그레이션 |
| --- | --- | --- |
| `hiring_baseline` | 종목별 채용 검색량 계절성 기준선(평균·분기 보정 계수). 채용 급증 판정의 비교 기준 | 006 |
| `hiring_signals` | HiringAnalyzer 분석 결과. 일자별 공고 수·기준선 대비 상대강도·급증 여부 저장 | 015 |
| `hiring_sources` | 기업별 공식 채용 사이트 크롤러 설정(크롤러 유형/클래스/URL). `(stock_id, crawler_type)` 단위, 기업 추가/변경의 Single Source of Truth | 016 |

## Zone D — Processing (007_processing.sql)

raw 계층을 정규화 계층으로 변환하는 처리 영역.

| 테이블 | 역할 |
| --- | --- |
| `processing_queue` | 정규화 작업 큐. Collector가 등록하고 Normalizer가 소비. `stock_id` nullable(DataLab은 카테고리 단위), `source_raw_ids` 배열로 처리 대상 추적 |
| `source_documents` | 정규화된 출처 문서. 원본(`raw_documents`)에 신뢰도(`reliability_level`)·공식 여부(`is_official`)를 부여한 분석용 계층 |
| `signal_events` | 정규화된 시그널 이벤트(이벤트 유형·방향·임팩트). Agent 분석의 기본 입력 단위. `event_hash`로 중복 방지 |
| `signal_metrics` | 시그널 이벤트에 딸린 정량 지표(name-value). 수치 데이터의 DB 고정값 |
| `validation_logs` | 정규화/분석 단계의 검증 결과 로그. 배열 FK를 강제 못하는 source trace를 검증·기록 |

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
| `final_signals` | 사용자/프론트에 노출되는 최종 시그널. `final_score`·신호·게시 여부·법적 고지 보유. `is_current` 부분 유니크+트리거로 조합당 현재 시그널 1건 |
| `score_history` | 최종 점수 변동 이력. 시그널/분석 결과별 점수 추적 |
| `backtest_results` | 최종 시그널의 사후 성과(5일 변화율·적중 여부) 백테스트 |

## Zone F — User 확장 (010_users_billing_extend.sql)

| 테이블 | 역할 |
| --- | --- |
| `signal_subscriptions` | 사용자 구독 상태. 활성 구독 1건만 허용(부분 유니크) |
| `watchlists` | 사용자 관심종목. `(user_id, stock_id)` 유니크 |
| `signal_journals` | 사용자가 시그널에 남긴 투자 일지/견해 |
| `user_signal_reads` | 사용자별 시그널 읽음 표시. `(user_id, final_signal_id)` 유니크 |
| `social_accounts` | 소셜 로그인 연동(provider·provider_user_id) |
| `portone_verifications` | PortOne 본인인증 기록(imp_uid 등) |
| `terms_agreements` | 약관 동의 이력(약관 유형·버전별). `(user_id, terms_type, version)` 유니크 |

## Zone G — Admin (011_admin.sql)

| 테이블 | 역할 |
| --- | --- |
| `admin_accounts` | 관리자 계정(이메일·비밀번호 해시·활성 여부) |
| `admin_sessions` | 관리자 세션 토큰과 만료 시각 |

## Legacy — report MVP (013_legacy_report_mvp.sql) ⚠️ 폐기 예정

신규 코드 참조 금지. 공용 경로(`raw_documents` → `report_raw_details` → `report_chunks`)로 이전 후 DROP 예정.

| 테이블 | 역할 |
| --- | --- |
| `report_raw` | (레거시) report RAG MVP가 마이그레이션 체계 밖에서 쓰던 리포트 원본. 문자열 날짜 등 구 스키마 |
| `report_signal` | (레거시) 위 원본 기반 리포트 시그널(방향·점수·의견 JSON) |

## 시스템 테이블

- `schema_migrations(filename PK, checksum, applied_at)` — `database/migrate.py`가 자동 생성·관리하는 적용 원장. 마이그레이션 파일로 만들지 않으며 이 표에도 포함하지 않는다.

## 트리거 (012_triggers.sql)

테이블은 아니지만 참고: `updated_at` 자동 갱신 트리거 일괄 부착 + `final_signals.is_current` 단일화 트리거(`set_final_signal_current`) 등 2종 함수.
