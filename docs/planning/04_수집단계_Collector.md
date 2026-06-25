# 04. 수집 단계 (Collector) — 상세 스펙

> Signal α 하위 문서 | 상위: [00_메인_기획서.md](00_메인_기획서.md)

---

## 4.1 공통 스펙

모든 Collector는 아래 공통 규약을 준수한다.

| 규약 | 내용 |
| --- | --- |
| LLM | 호출하지 않는다 |
| 처리 범위 | 외부 소스에서 원본 데이터만 수집·저장. 가공·분석은 하지 않는다 |
| 저장 형식 | `raw_documents`(공통 메타데이터) + 소스별 `*_raw_details` 테이블에 원본 저장 |
| 후속 처리 | `processing_queue`에 NORMALIZE 작업 등록 |
| 우선순위 태깅 | 즉시 처리는 `immediate`, 일반은 `batch` |
| 실패 격리 | 특정 소스 수집 실패는 해당 소스에만 국한, 다른 모듈로 전파 안 됨. 실행 로그는 `collector_runs`에 기록 |
| 인터페이스 | 공통 베이스 인터페이스 상속 (`app/collectors/base.py`) |

> Report 경로의 PDF 다운로드, 파싱, LLM 보강은 `collect_report`가 아니라 후속 queue 단계(`process_report`, `embed_report`, `analyze_report`)에서 수행한다. Collector는 리포트 목록 메타데이터와 원천 URL을 저장하고 후속 작업을 등록하는 책임만 가진다.

---

## 4.2 소스별 수집 기간 차등화

데이터마다 변화 속도가 다르므로 수집 기간과 비교 기준을 다르게 설계한다. 획일적 기간은 노이즈를 유발한다.

| 소스 | 변화 주기 | 수집 기간 | 비교 기준 | 설계 이유 |
| --- | --- | --- | --- | --- |
| 공시 (DART) | 수시 | 최근 90일 | 건별 즉시 | 고임팩트 공시는 발생 즉시 처리 |
| 리포트 | 분기 집중 | 최근 90일 | 직전 분기 | 컨센서스 반영 기간 |
| 채용 (Hiring) | 주~월 | 최근 90일 | 직전 30일 vs 그 전 60일 | 채용 공고 게시 주기와 분기 사업 확장 반영 |
| 특허 (Patent) | 분기~년 | 최근 180일 | 직전 분기 vs 전년 동분기 | 출원-공개 시차가 커 장기간 관찰 필요 |
| 검색 (DataLab) | 일~주 | 최근 30일 | 직전 7일 vs 그 전 21일 | 검색량은 즉각 반응, 짧게 봐야 급등 포착 |
| 주가 (Price) | 실시간 | 장중 실시간 폴링 + 과거 120영업일 백필 `[계획]` | 이동평균·추세 | 기술적 지표 산출에 필요한 최소 기간. 백필 전까지 분석기는 `insufficient_history` 상태가 정상 |

---

## 4.3 수집기 명세 통합표

| 항목 | C-1 Dart | C-2 Report | C-3 Hiring | C-4 Patent | C-5 DataLab | C-6 Price |
| --- | --- | --- | --- | --- | --- | --- |
| 상세 저장 테이블 | dart_raw_details | report_raw_details · report_chunks · `[계획] report_valuation_facts` | hiring_raw_details | patent_raw_details | datalab_raw_details | price_snapshots · ohlcv_data |
| 데이터 소스 | DART OpenAPI | 네이버 증권 + PDF | 사람인·잡코리아·기업 채용 페이지 | KIPRIS OpenAPI | 네이버 DataLab API | 키움증권 REST API |
| 수집 방식 | REST API | 크롤링 + PDF URL/메타 저장 | 멀티소스 크롤링 | API 호출 | API 호출 | REST 폴링 (OAuth) |
| 수집 기간 | 90일 + 즉시 | 90일 | 90일 | 180일 | 30일 | 실시간 + 120영업일 백필 `[계획]` |
| 우선순위 | 고임팩트 immediate / 그 외 batch | batch | batch | batch | batch | 장중 상시 (데몬) |
| 운영 제약 | LLM 없음 | 공개 접근 리포트만, 원문 사용자 노출 금지 | IP 차단 대비 (Known Issue) | — | 정책 변경 시 대체 | 결측·정렬 보장, 7일 초과 stale 표시 |
| 담당 | 성진 | 은진 | 광현 | 이슬 | 이슬 | 규태 |
| 구현 (main) | 구현 | 구현 | 구현 | 부분 | 부분 | 구현 |

---

## 4.4 수집기별 상세

### C-1. Dart_Collector — 구현

- **데이터 소스**: DART OpenAPI (opendart.fss.or.kr) — 무료, 일 10,000건
- **즉시 처리 대상**: 공급계약 · 실적발표 · 자사주매입 → 감지 즉시 분석 트리거
- **배치 처리 대상**: 단순 공고류 → 자정 일괄 처리
- **저장 시 태깅**: priority(immediate/batch)까지만. LLM 호출 없음
- **구현 메모**: corp_code 동기화(`dart_corp_codes`), 수집 상태 추적(`dart_collection_states`), 스케줄러(`app/orchestrator/dart/scheduler.py`) 포함

### C-2. Report_Collector — 구현

- **1차 수집**: 네이버 금융 리서치 목록에서 리포트 제목, 증권사명, 발행일, PDF URL 등 메타데이터를 수집
- **후속 처리**: 저장된 raw 문서마다 `process_report` 작업을 등록하고, PDF 다운로드·파싱·LLM 보강은 후속 queue handler가 수행
- **수집 대상 리포트**: 실적 분석(Earnings Review), 이벤트 노트(Event Note), 정기 분석(Company Report), 실적 전망(Earnings Preview)
- **수집 제외**: 산업·시장·전략 리포트처럼 종목 특정 데이터가 부족한 문서
- **운영 방침**: 공개 접근 가능한 리포트만 사용하며, PDF 원문과 긴 원문 청크는 사용자에게 노출하지 않음
- **저장소**: canonical queue 경로는 GCS 또는 local report storage를 사용하며, 저장 key는 `reports/{stock_code}/{publish_date}_{firm_slug}_{source_hash8}.pdf` 형식
- **구현 메모**: PDF 추출 → 청크(`report_chunks.chunk_text`) → BGE-M3 임베딩 → RAG 검색. 밸류에이션 재해석 전략은 후속 `[계획] report_valuation_facts`에 구조화 fact를 저장하는 방향으로 확장

### C-3. Hiring_Collector — 구현

- **데이터 소스**: 사람인 중심 + 잡코리아 + 기업 직영 채용 페이지(삼성·SK하이닉스·네이버·카카오·현대기아·크래프톤·하이브/SM 등) 멀티소스 크롤링 (`app/collectors/hiring/sites/`)
- **시그널 의미**: 사업 확장 방향의 선행 지표. 채용은 실제 예산 집행의 흔적
- **운영 제약**: IP 차단 대비 요청 간격·User-Agent 설정 (Known Issue)
- **구현 메모**: 키워드 생성기(`keyword_generator.py`)·기준선 테이블(`hiring_baseline`) 포함

### C-4. Patent_Collector — 부분 구현

- **데이터 소스**: KIPRIS OpenAPI (특허청) — 무료
- **시그널 의미**: R&D 투자 방향 변화. 신규 기술 카테고리 첫 출원은 사업 피봇 징후
- **수집 기간이 긴 이유**: 출원-공개까지 시차가 커 180일로 관찰
- **구현 현황**: API 클라이언트(`kipris_client.py`)와 출원인 별칭 매핑(`applicant_aliases.py`)까지 구현. 수집 파이프라인 본체는 `[계획]`

### C-5. DataLab_Collector — 부분 구현

- **데이터 소스**: 네이버 DataLab 공식 API — 무료
- **시그널 의미**: 브랜드·제품 검색량 급등은 소비자 수요 변화의 선행 신호
- **수집 기간이 짧은 이유**: 검색량은 즉각 반응하므로 30일로 짧게 봐야 급등 포착
- **구현 현황**: API 클라이언트(`naver_datalab_client.py`)와 카테고리-종목 매핑 스키마(`datalab_categories`·`datalab_category_stocks`)까지 구현. 수집 파이프라인 본체는 `[계획]`

### C-6. Price_Collector — 구현 (키움 REST 실시간 폴링)

- **데이터 소스**: 키움증권 **REST API** (App Key/Secret + OAuth, 모의 `mockapi.kiwoom.com` / 실전 `api.kiwoom.com`)
- **실행 형태**: 별도 컨테이너가 아니라 **agent-worker 내 lifespan 백그라운드 데몬** (`app/collectors/price/runner.py`, `PRICE_COLLECTOR_ENABLED`). 이전 OpenAPI+/COM 기반 Windows 배치 수집기는 revert로 제거됨
- **수집 동작**: 장중(평일 09:00~15:30 KST) `stocks.is_target` 종목을 기본 60초 간격 폴링(ka10001 주식기본정보), 장 마감 +30분에 투자자 순매수 확정치 1회(ka10059)
- **저장**: `price_snapshots`(장중 스냅샷) + `ohlcv_data`(당일 UPSERT + 수급 확정치) + `collector_runs`(실행 로그)
- **정합성 관리**: 결측·날짜 정렬 보장. 7일 초과 시 stale 표시 (분석 단계에서 처리). 데몬 중복 기동은 Postgres advisory lock으로 방지
- **후속 작업** `[계획]`: 120영업일 과거 일봉 백필(ka10081), 주/월/년봉·업종 지수 수집
- **용도**: 주가 분석기(A-5) 입력 및 백테스팅 검증용. 상세는 [`docs/price-collector.md`](../price-collector.md)·[`docs/spec/kiwoom-rest-spec.md`](../spec/kiwoom-rest-spec.md)

---

*상위 문서로 돌아가기 → [00_메인_기획서.md](00_메인_기획서.md)*
