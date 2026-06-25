# 채용공고 · 데이터랩 키워드 ML/DL 모델 선정 추천 보고서

> **대상 프로젝트**: signal-alpha (FastAPI 단일 백엔드, 멀티에이전트 투자 신호 분석)
> **작성일**: 2026-06-23
> **스코프**: 5개 입력 소스 중 **① 채용공고 ② 네이버 데이터랩 키워드** 두 소스에 도입할 ML/DL 후보군
> **결과물 성격**: 직접 벤치마크·선정을 위한 의사결정용 조사 보고서 (코드·의존성 변경 없음)
> **선례 문서**: [`timeseries-forecasting-model-selection.md`](./timeseries-forecasting-model-selection.md) — 본 보고서는 그 위에서 "두 소스 전용" 모델 선택지를 구체화한다.

---

## 1. 요약 (Executive Summary)

- 채용공고·데이터랩은 시계열 한 개로 다룰 대상이 아니라 **이종 대체데이터(alternative data)**다. 따라서 **하나의 모델**이 아니라 **3개 레이어**로 나눠 후보를 고른다.
  - **(A) 표현 / NLP** — 텍스트·키워드에서 신호 추출 (DL)
  - **(B) 이상탐지 · 시계열 피처화** — 키워드 급증·채용 강도 정량화 (ML)
  - **(C) 통합 · 예측 본체** — 두 소스를 피처로 묶어 변동성/거래량 신호 산출 (ML/DL)
  - **(D) (선택) 소스별 독립 예측** — 키워드 관심도·채용 강도 자체 예측
- **현재 상태**: 두 소스 모두 **순수 룰베이스**다. 도입하는 ML/DL은 전부 신규 추가다.
- **즉시 재사용 가능한 자산**: BGE-M3 임베딩(`sentence-transformers`)·LLM(`openai`)이 이미 설치돼 있어 **(A)는 추가비용이 가장 낮다.** 새로 비용이 드는 부품은 (C)의 LightGBM 정도다.
- **선정 원칙**: 모든 후보는 **현행 룰베이스 분석기 + naive 베이스라인을 워크포워드 백테스트에서 통계적으로 유의하게 이겨야** 채택한다. 못 이기면 룰을 유지한다.
- **권고 도입 순서**: **(A) 임베딩 피처 → (B) 키워드 급증·채용 nowcast → (C) LightGBM 통합 → (D) 선택적 독립예측.**

---

## 2. 현황 — 두 소스는 지금 어떻게 처리되나

| 항목 | 채용공고 (HIRING) | 데이터랩 키워드 (DATALAB) |
|---|---|---|
| 분석 방식 | **룰베이스** (`app/analyzers/hiring/hiring_analyzer.py`) | **룰베이스** (`app/analyzers/datalab/`) |
| 핵심 로직 | 14일 이동평균 대비 상대강도 + 분기 계절가중치 + spike(≥150%) | 키워드 리스트 기반 polarity(demand/supply), 룰 기반 `is_spike` |
| 데이터 성격 | 이벤트·불규칙, 공고 1건=1행(`job_count` 항상 1) | 일별 연속, `search_index` 0~100 정규화 |
| 보유 필드 | `keyword`(직무명)·`job_description`·`tech_stack[]`·`closing_date`·`sector`·`observed_date` | `search_index`·`previous_search_index`·`change_pct`·`is_spike`·`period_type`·device/gender/age·`keyword_group`·polarity |
| 종목 매핑 | 회사명 → `stocks` 매칭 | category → stock weight (`datalab_category_stock_map.csv`) |
| 미활용 자산 | **`job_description`·`tech_stack` 텍스트 거의 미사용** | 세그먼트(device/gender/age), 키워드 패널 구조 |
| 스냅샷 | (없음 — 추가 권장) | `export_datalab.py` → parquet 이미 존재 |

**이미 보유한 ML 인프라**: `sentence-transformers`(BGE-M3, torch 동반) · `openai`(LLM 파싱) · `signal-alpha-vol-models`(변동성). DART는 이미 `app/analyzers/dart/embedding_features.py`로 임베딩 사용 중.
**미설치**: 트리/통계 ML(`scikit-learn`·`lightgbm`·`statsmodels`·`prophet`). 채택 시 `pyproject.toml`에 추가 필요.

> 핵심 시사점: **채용 텍스트와 데이터랩 세그먼트가 거의 버려지고 있다.** ML/DL 도입의 1차 가치는 "예측"보다 **이 미활용 신호를 피처로 살리는 것**이다.

---

## 3. 레이어 A — 표현 / NLP (텍스트·키워드 → 신호) · DL

### A-1. 채용공고 텍스트 임베딩 (`job_description`, `tech_stack`, 직무명)

| 후보 | 성격 | 적합성 |
|---|---|---|
| **BGE-M3** | 다국어 임베딩 (이미 설치) | **1순위 — 인프라 재사용, 추가비용 0** |
| **KURE-v1** | 한국어 검색 임베딩 SOTA급 | 한국어 정밀도 필요 시 대안 |
| **KoSimCSE / ko-sbert** | 한국어 문장 임베딩 경량 | CPU 저비용 대안 |
| **multilingual-e5-large** | 다국어 임베딩 | 비교 베이스라인 |

**용도**
- 직무설명 임베딩의 **주간 드리프트** = 기업 전략전환 선행 피처(예: 특정 기업이 갑자기 AI/반도체 직군을 대량 채용 → 사업 확장 시그널).
- 직무명 → 표준 카테고리 **무지도 클러스터링**.
- `tech_stack` 동의어 **정규화**(예: "파이토치"/"PyTorch"/"torch" 통합).

### A-2. 채용 텍스트 분류 인코더 (직무 카테고리 · 채용 의도)

| 후보 | 비고 |
|---|---|
| **KR-FinBERT** | 한국어 **금융 도메인** BERT — 본 프로젝트에 가장 적합. "확장 / R&D / 감원" 채용 의도 분류, 직무 카테고리 미세조정 |
| **KLUE-RoBERTa / KoELECTRA** | 범용 한국어 강 baseline, 라벨 적을 때 견고 |

- **라벨 부트스트랩**: LLM(`openai` 보유) zero-shot으로 약라벨 생성 → 인코더 미세조정 → 운영 시 비용·지연 절감.

### A-3. 데이터랩 키워드 polarity 분류 (demand / supply)

현재 키워드 리스트 룰을 대체.
- **소량 라벨**: 임베딩(BGE-M3) + LogisticRegression / kNN — 가장 가벼움.
- **라벨 충분**: KR-FinBERT 미세조정.
- LLM zero-shot은 약라벨 생성기로 활용.

---

## 4. 레이어 B — 이상탐지 · 시계열 피처화 · ML

### B-1. 데이터랩 키워드 급증 탐지 (현행 `is_spike` 룰 업그레이드)

| 후보 | 성격 | 비고 |
|---|---|---|
| **STL + robust z-score** | 계절성 분해 후 잔차 이상치 (`statsmodels`) | **필수 베이스라인.** 가볍고 해석 쉬움 |
| **Seasonal-Hybrid ESD (S-H-ESD)** | 계절 시계열 급증 탐지 표준 | 주기성 강한 검색량에 적합 |
| **Prophet** | 변화점(changepoint)·추세전환 탐지 | 관심도 레짐 변화 감지, 해석 용이 |
| **Matrix Profile (STUMPY)** | 모티프/이상 패턴 | "반복되는 급증" vs "새로운 급증" 구분 |
| **IsolationForest** | 다변량 이상 | 다중 키워드·세그먼트 동시 이상 탐지 |

- **선행성 측정**: lagged cross-correlation / Granger(베이스라인) / TLCC로 키워드 → 변동성·거래량 리드랙(lead-lag)을 정량화.

### B-2. 채용공고 강도 nowcast (현행 상대강도 룰 업그레이드)

| 후보 | 성격 | 비고 |
|---|---|---|
| **Croston / TSB** | 간헐 수요(intermittent demand) 예측 표준 | 채용은 희소·불규칙 카운트 → **1순위 베이스라인** |
| **Poisson / NegBinom GLM** | 카운트 회귀 | 계절·기업더미·텍스트피처 결합 가능 |
| **BSTS / Prophet** | 구조적 시계열 + 불확실성 | 추세·계절 분해된 채용 강도 nowcast |

- **텍스트 결합**: A-1 임베딩 드리프트를 강도 피처로 합류 → "양적 급증"과 "질적 전환"을 함께 포착.

---

## 5. 레이어 C — 통합 · 예측 본체 (두 소스를 피처로) · ML/DL

*(기존 timeseries 보고서와 일관: 통합 본체 = 트리/TFT, 파운데이션 모델 = 주가채널 피처생성기)*

| 후보 | 강점 | 위치 |
|---|---|---|
| **LightGBM / CatBoost** | 이종 tabular·결측에 강함, SHAP로 소스별 기여 설명 | **통합 본체 1순위.** 키워드 z-score/급증 + 채용 변화율/임베딩드리프트를 흡수 |
| **TFT (Temporal Fusion Transformer)** | 정적 + 과거관측 + 미래known 공변량 네이티브, 다중horizon·확률·변수중요도 | 데이터 충분 시 본체 대안 |
| **DeepAR** | 양수·곱셈적 카운트 확률예측 | 채용·키워드 카운트 채널 전용 |

---

## 6. 레이어 D — (선택) 소스별 독립 예측

- **키워드 관심도 자체 예측**: **Chronos-2** 또는 **TinyTimeMixer (IBM Granite TTM)** — 경량·CPU·외생채널·zero-shot.
- **채용 강도 예측**: §B-2 카운트 모델.

---

## 7. 베이스라인 (반드시 이겨야 채택)

| 타깃 | 베이스라인 |
|---|---|
| 키워드 급증 | 현행 룰(z-score), STL 잔차 |
| 채용 강도 | 현행 `HiringAnalyzer` 상대강도, Croston |
| 통합 신호 | 동일 피처를 직접 넣은 LightGBM |

> 이 베이스라인을 워크포워드에서 유의하게 이기지 못하면 해당 타깃은 **룰 유지가 정답.**

---

## 8. 평가 프로토콜 (vol-benchmark 테스트베드 연계)

- **워크포워드(rolling-origin) 백테스트** + **point-in-time 강제**: 채용은 `published_at`, 키워드는 `observed_date` **이후 시점에만** 사용 → look-ahead 차단(**#1 리스크**, 가짜 알파 방지).
- **메트릭 매핑**
  - 이상탐지: PR-AUC · precision@k
  - 분류(polarity/직무): macro-F1
  - nowcast: MASE · CRPS
  - 통합 기여도: SHAP · permutation importance
  - 선행성: lead-lag IC
- **스냅샷**: `export_datalab.py` parquet 재사용. **채용도 동일 export 스크립트 추가 권장**(테스트베드 입력 표준화).

---

## 9. 배포 제약 / 도입 우선순위

- **보유 자산 재사용**(BGE-M3·LLM) → (A) 우선 착수 시 비용 최저.
- 추가 부품은 **CPU 경량**(LightGBM/CatBoost, STL/Prophet, TTM) 선호, torch 대형 모델 회피.
- **권고 순서**: (A) 임베딩 피처 → (B) 키워드 급증·채용 nowcast → (C) LightGBM 통합 → (D) 선택적 독립예측.
- **의존성 메모**: 채택 시 `pyproject.toml`에 `lightgbm` / `scikit-learn` / `statsmodels`(또는 `prophet`) 추가 필요. 현재 미설치.

---

## 10. 최종 권고 매트릭스

| 레이어 | 1순위 | 대안 | 필수 베이스라인 |
|---|---|---|---|
| A. 채용 텍스트 표현 | **BGE-M3**(재사용) | KURE-v1 / KR-FinBERT(분류) | LLM zero-shot |
| A. 데이터랩 polarity | 임베딩+LogReg | KR-FinBERT 미세조정 | 현행 키워드 룰 |
| B. 키워드 급증 | **STL+robust z** | S-H-ESD / Prophet / Matrix Profile | 현행 `is_spike` 룰 |
| B. 채용 강도 | **Croston/TSB** | NegBinom GLM / BSTS | 현행 상대강도 룰 |
| C. 통합 본체 | **LightGBM/CatBoost** | TFT / DeepAR | LightGBM(피처 직접) |
| D. 독립 예측(선택) | Chronos-2 / TTM | — | naive last-value |

> 핵심 메시지: ML/DL 도입의 가장 큰 즉효는 **버려지던 채용 텍스트·데이터랩 세그먼트를 피처로 살리는 것**이며, 예측 본체는 파운데이션 모델이 아니라 **트리(LightGBM)**가 1순위다. 모든 후보는 **현행 룰 + naive를 이겨야** 채택한다.

---

## 부록: 범위 밖 (명시)

- 모델 다운로드 / 추론 / 미세조정 코드 작성 — 하지 않음.
- signal-alpha 코드 수정 · 의존성 추가 — 하지 않음.
- 본 문서는 후속 벤치마크·선정을 위한 **의사결정용 조사 보고서**다.
