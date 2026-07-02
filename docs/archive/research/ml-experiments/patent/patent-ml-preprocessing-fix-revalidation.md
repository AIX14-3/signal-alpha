# 특허 단독 ML — 전처리/방법론 결함 교정 후 엄밀 재검증 (2026-06-26)

> 전날 "특허 단독 방향성 알파 기각"이 전처리 결함(횡단면 정규화 부재)·LLM 부재·방법론(pooled IC) 때문일 수 있다는 의심을, 3개 감사로 검증→코드 교정→재실험으로 검정. 34종목 prod 데이터 재사용($0).
> 
> **한 줄 결론: 어제 결론은 맞았으나 이유가 틀렸다 — 정규화 교정 후 장기(h=60) 트리 신호가 일관 양(+0.09, 라벨셔플 permutation p<0.001)으로 살아났지만, 비겹침(signal-step=60) 검정에서 소멸하고 겹침↓에 따라 단조 감소 → 자기상관 artifact로 판명. count 기반 특허 방향성 알파는 정직한 방법론으로 견고하게 무신호.**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 (Windows 11, CPU) |
| Python / 라이브러리 | 3.11.15 · scikit-learn 1.9.0 · scipy · pandas 3.0.3 |
| DB / 데이터 | Supabase prod `raw_documents`(GOOGLE_PATENTS, 147,288행/34종목) · 가격 FDR CSV |

## 2. 동기 — 3개 감사로 확인한 결함

| 결함 | 근거 | 영향 |
| --- | --- | --- |
| 🔴 횡단면 정규화 부재 | indicators 카운트 절대값(삼성 5만 vs 소형 50=1000배), StandardScaler는 전역 | 모델이 종목 크기(정체성) 학습 |
| 🔴 LLM enrich 76% 공백 | prod DB: 8/34종목만 enrich | significance 피처 死 → LLM 가설 미검정 |
| 🟠 MNAR median 임퓨트 | 비어있는 significance를 중앙값 채움 | "대형 8종목" 식별자 주입 |
| 🟠 pooled rankIC | test청크 전체 1회 Spearman | 횡단면+시계열 혼합 |
| 🟢 publication_date | 100% 존재 | (드롭 우려 기우) |

## 3. 교정 (코드 변경, app/ml/research — UNTRACKED)

1. **횡단면 정규화** `patent_dataset._cross_sectional_normalize`: 날짜내 카운트형 피처 rank/z-score(`--xs-normalize`). 크기 레벨 제거, 순위만 보존.
1. **死/정체성 피처 제거** `--feature-set count`: significance 3종·new_category_count 제외(11→7피처).
1. **날짜별 횡단면 IC** `evaluation._xs_rank_ic`(`rank_ic_xs`): test fold 내 날짜별 Spearman 평균(정석 quant IC), pooled와 병기.
1. **permutation null**(`run_permutation`, rank_ic_xs 기준) + **signal-step**(겹침 제어) CLI 배선.
## 4. 방법론

- 16모델 워크포워드(날짜경계 분할) · 라벨=KS11 대비 초과수익 방향 · neutral band.
- 판정: rank_ic_xs가 모델 전반 일관 + permutation p<Bonferroni(0.05/64) + **비겹침에서도 유지**.
## 5. 결과

### 레버1 — 단기 (lookback 60·h 5·step 5·정규화 rank·count-only)

samples=4093·34종목. rank_ic_xs 최고 grad_boost +0.017(sd 0.034), **전 모델 Dbase≤0**. → 무신호(단기는 진짜 평평).

### 레버4 — 장기 (lookback 360·h 60·step 5·정규화 rank·count-only)

samples=4373. **트리 7종 일관 양**: random_forest rank_ic_xs +0.095·extra_trees +0.095·hist_grad_boost +0.092·grad_boost +0.075·decision_tree +0.065. 선형(ridge/lda/logistic)은 어제 −0.12에서 **≈0으로 교정**됨. Dbase+, decile +3~10.

### permutation null (라벨셔플 150회, 트리 모델)

| 모델 | obs | null_mean | null_sd | p |
| --- | --- | --- | --- | --- |
| hist_grad_boost | +0.092 | +0.000 | 0.017 | 0.000 |
| random_forest | +0.096 | +0.001 | 0.017 | 0.000 |
| extra_trees | +0.095 | −0.001 | 0.019 | 0.000 |
| decision_tree | +0.065 | +0.001 | 0.016 | 0.000 |

→ ~5σ, Bonferroni(0.00078) 통과. **라벨셔플 기준으론 유의.**

### 결정적 검정 — 겹침(자기상관) 제거

60일 forward + 5일 신호간격 = forward 윈도우 92% 중첩. signal-step을 키워 겹침 제거:

| 겹침 | step | 트리 rank_ic_xs | Dbase |
| --- | --- | --- | --- |
| 92% | 5 | **+0.09** | +양 |
| 50% | 30 | +0.05 | −음 |
| 0% | 60 | **≈0 (산포, 일부 −)** | −음 |

→ 겹침↓에 따라 **단조 감소→0**. 라벨셔플 permutation은 자기상관을 파괴하므로 이 artifact를 못 잡음(null 분산 과소→p 과대낙관). 대형·중소형 분해 시 양쪽 다 겹침 하에서만 신호(artifact의 일반성).

## 6. 해석 · 판정

- **레버1 무신호**(단기·비겹침 등가) + **레버4 신호=겹침 자기상관 artifact**(비겹침 소멸·단조감소) → **count 기반 특허 방향성 알파 기각**(이번엔 정직한 방법론으로).
- 전날 "트리+/선형− 상충=과적합" 진단은 부정확했음: 실제로는 (a) 선형 음수=크기 스케일 artifact(정규화로 제거), (b) 트리 양수=겹침 자기상관 artifact(비겹침으로 제거). **두 artifact가 겹쳐 우연히 "상충"처럼 보였던 것.**
- **교훈**: 정규화·날짜별IC·permutation·**비겹침 검정**을 표준 게이트로. 라벨셔플 permutation 단독은 겹침 데이터에서 anti-conservative.
## 7. 한계

- LLM significance/novelty 가설은 **여전히 미검정**(enrich 76% 공백, $11·~2h 비용 + 이 환경 백그라운드 강제종료 제약으로 보류). 단 count artifact 확인 후 우선순위 낮다고 판단해 사용자 합의로 중단.
- 비겹침 표본 작음(381, 23날짜·3fold)→저검정력. "겹침서 유의→비겹침서 소멸 + 단조감소"의 패턴이 핵심 근거(절대 무신호 증명은 아님).
- 2021~2023 단일 레짐.
## 8. 산출물

- 코드(UNTRACKED, app/ml/research): `patent_dataset.py`(_cross_sectional_normalize·feature exclude)·`evaluation.py`(_xs_rank_ic·dates)·`bakeoff.py`(--xs-normalize/--feature-set/--signal-step/permute)·`patent_db.py`·`report.py`. 테스트 `tests/test_ml_patent_xs.py`(4). 전체 ML 테스트 30 GREEN.
- 재현(레버4 비겹침): `bakeoff --source patent-db --tickers <34> --lookback 360 --horizon 60 --band 0.5 --folds 3 --signal-step 60 --xs-normalize rank --feature-set count --prices-csv prices34.csv --benchmark KS11`.
## 9. 다음 단계

- [x] 특허 단독 방향성 트랙 **종료**(엄밀 무신호 확정).
- [ ] **다중소스 융합** — 특허·채용·DataLab 결합 횡단면(aggregator). 각 소스 개별 무신호여도 잔차/비선형 결합 알파 가능성.
- [ ] (보류) LLM 의미 피처는 융합 맥락에서 재투입 검토. 모든 향후 ML 실험에 **비겹침 + permutation** 게이트 표준화.

---

관련 메모리: [[patent-ml-rejected]] · [[ml-bakeoff-datalab-result]] · [[attention-lead-lag-evidence]] · 직전 리포트 `2026-06-26-patent-ml-universe34-xs-lf.md`
