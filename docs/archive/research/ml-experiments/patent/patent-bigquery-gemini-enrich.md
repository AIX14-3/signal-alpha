# 특허 BigQuery 적재 + Gemini LLM enrich + 분석기 신호 검증 (2026-06-25)

> 대형주 특허를 KIPRIS(월 1,000회 무료 한도) 대신 Google Patents(BigQuery 공개 데이터)로 벌크 적재하고, Gemini로 특허 중요도(significance)를 농축해 특허 분석기가 신호를 내는지 실제 규모로 검증했다. 실적재(Supabase prod) + 실 LLM 호출(paid tier).
> 
> **한 줄 결론: 삼성·SK하이닉스·NAVER 2016~2023 특허 61,999건을 적재·전수 enrich(누적 ~$6.30) 완료, 분석기가 윈도우별로 일관된 양수 신호 산출(8년: 삼성 +0.964·SK +0.613·NAVER +0.804) — 파이프라인·신호생성은 검증됐으나 "신호가 수익률을 예측하는지"(백테스트)는 본 실험 범위 밖.**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 Windows 11 (GPU 불필요) |
| Python / 라이브러리 | 3.11 (uv) · google-cloud-bigquery · asyncpg · python-dotenv |
| BigQuery | `patents-public-data.patents.publications`, 청구 프로젝트 `patent-bq-reader` (gcloud ADC) |
| LLM | Gemini `gemini-2.5-flash-lite` (paid/standard tier) — 단가 $0.10/$0.40 per 1M tok |
| DB / 데이터 출처 | Supabase prod (서울 리전 ap-northeast-2) |

## 2. 실행 메타

- 코드 위치 / 브랜치: `services/agent-worker/scripts/*` — **PR #457** (`feat/patent-bigquery-enrich`, CI GREEN, 미머지). collector 리팩터 포함(이슈 #456).
- 타깃: 예측 라벨 없음. 본 실험은 **데이터 적재 + LLM enrich + 분석기 신호 생성**의 파이프라인/신호 검증(predictive backtest 아님).
- 선행 조건: prod `patent_raw_details`에 `llm_features`/`llm_status` 컬럼 추가(마이그 019, 사용자가 SQL Editor로 ALTER) · Gemini 빌링(Cloud Prepay 선불) 활성화.
- 공통 파라미터: 분석기 `as_of=2023-12-31`, lookback 스윕(365/1100/3000일), enrich concurrency=8, 모델 flash-lite temp 0.2 JSON.
- 스윕 축: 적재 기간(2016~2023), 분석기 윈도우(2023/3년/8년).
## 3. 데이터 — 유니버스 · 자료 · 근거

- **유니버스**: 삼성전자(005930)·SK하이닉스(000660)·NAVER(035420). 한계: 3종목·전부 대형 R&D 다출원사라 "특허활동 양수"가 어느정도 baseline일 수 있음(상대 변별력은 분석기 보정에 의존).
- **수집 자료**:
| 자료 | 출처/API | 저장 | 행수 | 기간 |
| --- | --- | --- | --- | --- |
| 개별 특허(출원번호·제목·출원인·출원일·IPC) | BigQuery Google Patents (country_code='KR', assignee LIKE) | raw_documents/patent_raw_details (`source_name='GOOGLE_PATENTS'`) | 61,999 | 출원 2016~2023 |
| 초록(한국어) | BigQuery `abstract_localized` | patent_raw_details.extra_payload | 커버리지 100% (enrich 표본 전건) | 동일 |
| LLM 중요도 features | Gemini flash-lite | patent_raw_details.llm_features | 61,999 enriched | 동일 |

- **근거**: KIPRIS 무료=월 1,000회로 대형사 벌크 불가 → 과거=BigQuery, 최신=KIPRIS 전략. BigQuery 출원건수가 KIPRIS/현실과 일치(삼성 연 6~9천) 사전검증됨(2026-06-24).
- **무결성**: 적재 연도분포 검증 — 삼성 53,489 / SK 7,066 / NAVER 1,444 = 61,999, application_date NULL 0건, 적재 실패 0. 공동출원 4건은 전역 UNIQUE(application_no)로 1회만 적재(정상).
- **누수 차단**: 해당 없음(예측 모델 아님). 단 application_no는 BQ 네이티브 형식(`KR-…-A`)이라 KIPRIS 13자리와 교차 dedup 안 됨.
## 4. 방법론

- **enrich**: 특허 제목+초록 → Gemini로 significance·core_business_relevance·novelty·commercialization_stage·rationale 추출(0~1 클램프), `llm_features` 캐시(특허당 1회). 동시 8호출·429 백오프 재시도.
- **분석기**(production `PatentAnalyzer`): `patent_raw_details` 직접 로드(정규화 불필요). lookback 윈도우 내 출원을 recent/prior로 분할해 모멘텀 + 신규카테고리 + **LLM 평균 significance** 가중 → direction/score(tanh 합성).
- **판정 규칙**: "신호 있음" = 분석기가 data_status='ok'로 방향·점수를 산출하고, LLM significance가 점수에 반영(하이라이트에 명시)되며, 윈도우 변화에 논리적으로 반응.
## 5. 결과

### enrich (단계별)

| 단계 | 건수 | 성공/실패 | 비용 |
| --- | --- | --- | --- |
| phase1 소형검증 | 300 | 300 / 0 | $0.03 |
| 2023 | 10,141 | 10,139 / 2 | $1.04 |
| 2021~2022 | 18,394 | 18,388 / 6 | $1.90 |
| 2016~2020 | 33,164 | 33,137 / 27 | $3.33 |
| **합계** | **61,999** | **~61,964 / ~37 (0.06%)** | **≈ $6.30 (₩8,700)** |

- 토큰 사이즈(평균): 입력 ~465 / 출력 ~116 tok per call.
### 분석기 신호 (as_of 2023-12-31, 윈도우별)

| 윈도우 | 삼성전자 | SK하이닉스 | NAVER |
| --- | --- | --- | --- |
| 2023만 (365d) | +0.863 (mom +35%) | +0.647 (+8%) | +0.997 (+56%) |
| 3년 (1100d) | +0.813 (mom +28%) | +0.726 (+17%) | +0.401 (−18%) |
| 8년 (3000d) | +0.964 (mom +57%) | +0.613 (+5%) | +0.804 (+27%) |

- mean_significance: 삼성 0.71 / SK 0.72 / NAVER 0.68 (max 0.8~0.9).
- **재검증**: phase1 표본(종목당 100건) 3년 신호(삼성 +0.811)가 전수(25,719건, +0.813)와 거의 일치 → 소형 검증이 전수를 잘 예측(샘플 대표성 확인).
## 6. 해석 · 판정

- **채택**: BigQuery→DB→초록→Gemini enrich→분석기 신호 파이프라인이 만 건 규모에서 정상 작동(실패 0.06%). 신호는 윈도우별로 **일관된 논리**로 변동(NAVER: 3년추세 −18%지만 2023·8년은 증가 → 윈도우 의존 정상). LLM significance가 점수에 실제 기여.
- **비용/한도 발견(확정)**: flash-lite **무료 tier = 하루 20요청**(분당 아님) → 벌크 불가, paid 필수. paid 실단가 ≈ $0.0001/call(전수 $6.30).
- **미검증(중요)**: "이 신호가 미래 수익률을 예측하는가"는 **본 실험에서 안 봄**. 모든 종목이 항상 양수 → 대형 다출원사 baseline 가능성, 변별력은 별도 평가 필요.
## 7. 이상치 · 주의 / 한계

- application_no BQ 네이티브 형식 → KIPRIS와 교차 dedup 불가.
- 분석기 윈도우에 기존 mock/KIPRIS 특허 소수 혼입(`latest_application_date`가 2025로 표시되는 원인) — 신호는 BigQuery분이 지배.
- 2024~2025 출원은 BigQuery 18개월 공개지연으로 비어있음 → KIPRIS 보강 필요.
- 막판 enrich는 paid tier RPM throttle로 속도 저하(30s 백오프), 실패 누적 ~37건.
- 결과 CSV/JSON은 로컬 비추적.
## 8. 산출물

- 스크립트(PR #457): `scripts/backfill_patents_bigquery.py`(적재) · `enrich_patents_llm.py`(동시 enrich+분석기) · `phase1_patent_enrich_validate.py` · `pilot_enrich_patents.py`(비용측정). collector `ingest_records()`.
- 결과 JSON(로컬 비추적): `scratchpad/phase1.json`·`enrich2023.json`·`enrich2123.json`·`enrich1620.json`.
- 재현(전수 확대 예):
```plain text
  uv run --with google-cloud-bigquery python scripts/enrich_patents_llm.py \
    --start 2016-01-01 --end 2023-12-31 --concurrency 8 --as-of 2023-12-31 --lookback 3000
```

## 9. 다음 단계

- [ ] **신호 → 수익률 백테스트**: 특허 모멘텀/significance가 forward return을 예측하는지(IC/방향성) — 미검증 핵심.
- [ ] 최신분(2024~2025) KIPRIS 보강(월 할당량 리셋 후).
- [ ] **다중소스 융합**(특허+채용+DataLab) — 단일소스 한계 보완.
- [ ] 실패 ~37건 재처리(`llm_status='failed'` 리셋), application_no 정규화 검토.
- [ ] 종목 유니버스 확대(대형 baseline 변별력 확인).

---

관련 메모리: [[bigquery-patent-connection]] · [[ml-bakeoff-datalab-result]] · [[attention-lead-lag-evidence]]
