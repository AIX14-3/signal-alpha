# 채용(HIRING) ML — KOSPI 확장 유니버스(57종목) 본격 검정 (2026-06-29)

> 3종목(~330공고) 단독 검정의 통계력 부족을 풀기 위해 유니버스를 KOSPI 시총상위
> 200으로 확장(전수스캔 7842공고·175사)하고, 데이터가 충분한 **57종목**을 선별해
> 패널 ML로 채용↔미래주가방향을 본격 검정했다. 두 가지를 본다: ① 기존 **volume**
> (공고 플로우) 피처, ② 사용자 핵심 가설인 **duty(직무 mix)** 피처. 다중검정은
> **Benjamini–Hochberg FDR**로 보정한다.
>
> **한 줄 결론: N=57·표본 ~2,200으로 통계력을 크게 올렸지만, volume·duty·volume+duty
> 어느 것도 미래 초과수익 방향에 유의미한 신호가 없다. 전 패밀리(90셀) permutation+
> BH-FDR 생존 0, 최강 셀(volume+duty h20 knn rankIC +0.073)도 q=0.45로 탈락. duty(직무
> mix) 가설도 N=57에서 미지지(in-sample만 커지고 유의성 없음=과적합). 채용은 동행하되
> 선행하지 않음 — 특허·DataLab 단독 무신호와 일관.**

---

## 1. 배경 / 동기
- 3종목 채용 단독 = 무신호였으나 통계력(독립표본<3·~330공고)이 근본 제약이었다
  (`2026-06-25-hiring-ml-bakeoff.md`).
- 직전 세션에 유니버스를 KOSPI 시총상위 200으로 확장: stocks 39→208 시드, 가격 201종목,
  **채용 전수스캔(공고 2397→7842, 회사 3→175)**.
- 이번 세션: 데이터 충분 종목 선별 → 패널 ML 본격 검정 + duty 가설 + FDR.

## 2. 데이터 / 환경
| 항목 | 값 |
|---|---|
| DB | Supabase prod — **read-only** |
| HIRING raw_documents | 7,842건 (hiring_raw_details 100% 적재) |
| **duty_groups 커버리지** | **98.7%** (7,740/7,842; 2016~2023 연도별 94~100%) |
| 가격 | FinanceDataReader → `prices_kospi200.csv`(201종목+KS11, 2016~2024) |
| 하니스 | `app/ml/research/`(공용) + 신규 `stats.py`(BH-FDR)·duty 피처 |

## 3. 방법론
- **타깃**: h일 선도 초과수익(종목−KS11) **부호**(분류), neutral band 0.3%.
- **horizon 스윕**: 5·10·20·30·60 거래일. **신호일 간격 20거래일**(월간, 겹침 자기상관 완화).
- **선별(precise-rematch)**: `raw_documents.source_name`을 유니버스명에 **정확 정규화 매칭**
  (오귀속 SKC코오롱PI↛SKC 제외). 임계 **≥30공고 & ≥3개 연도** → **ML 유니버스 57종목**.
  - 매칭 7,644/7,842(97.5%), 모호/비유니버스 198건 드롭. 임계 통과 58 중 가격보유 57.
- **피처 3종 × 2계열(전부 point-in-time, observed_date ≤ as_of)**:
  - **volume**: `deseason_momentum`(계절보정 플로우 모멘텀)·`yoy_change`·`days_since_latest`.
  - **duty**: `tech_share`(태깅 직군 중 기술/R&D 비중; 24개 기술직군 명시집합)·
    `tech_share_yoy`·`tech_share_mom`(최근/이전 반 기술비중 차).
  - duty 데이터 = `hiring_raw_details.extra_payload->duty_groups`(자소설 145직군; 공고당
    평균 12.9직군 태깅). `job_category` 컬럼은 업종이라 직무 아님 → 미사용.
- **CV**: 확장창 워크포워드(날짜경계 누수차단), folds=5, seed=42.
- **모델**: baselines + 선형/판별/확률/트리/거리/커널/앙상블 16종.
- **판정지표**: `rank_ic_xs`(per-date 횡단면 rankIC) 중심 + accuracy vs baseline.
- **유의성**: 라벨셔플 **permutation**(per-model p) → **Benjamini–Hochberg FDR**로
  feature-set×horizon×model 패밀리 전체 보정(`app/ml/research/stats.py`, 단위테스트 6).

## 4. 결과 — 디스크립티브(전 horizon, N=57)
표본: feature-set별 horizon마다 ~2,120–2,232 (폴드 n≈1,490–1,567). up-rate 0.45–0.46.

### 4.1 volume 피처 — horizon별 best rankIC_xs
| h | best model | rankIC_xs | sd_xs | baseline 이김? |
|---|---|---|---|---|
| 5  | grad_boost     | +0.043 | 0.045 | No (Dbase≤0) |
| 10 | svm_rbf        | +0.038 | 0.052 | No |
| 20 | svm_rbf        | +0.050 | 0.007 | No |
| 30 | svm_rbf        | +0.034 | 0.043 | No |
| 60 | random_forest  | +0.038 | 0.070 | No |

→ best rankIC_xs ≤ 0.05, 4/5 horizon에서 sd≈mean(불안정). 어떤 모델도 majority baseline
   정확도를 의미있게 못 이김.

### 4.2 duty 피처 — h=20 예시
- best = naive_bayes rankIC_xs **+0.030**(sd 0.031); ridge/lda/logistic은 Dbase +0.007이나
  rankIC_xs +0.013로 미미. → **duty 단독도 volume보다 약함**(사용자 가설 디스크립티브 미지지).

## 5. 결과 — 유의성(permutation + BH-FDR)
> 패밀리 = feature-set(3) × horizon(5) × 모델(fast-6 코어: logistic·ridge·lda·naive_bayes·
> knn·decision_tree). permute=200. O(n²/n³)·앙상블 재적합 모델(GP·SVM·RF·voting·stacking·
> 부스팅)은 패널 규모에서 permutation 비현실적 → fast-6로 검정하고, 디스크립티브 위너
> (svm/RF/grad_boost)는 별도 타깃 permutation으로 보완(§5.2).

### 5.1 fast-6 전 패밀리 BH-FDR (확정 2026-06-29, 전 15셀 완주)
`hiring_fdr_aggregate.py --csv volume=… duty=… volume+duty=…` (permute=200, 스윕 16:09→16:50 완주):
- **family N = 90** (feature-set 3 × horizon 5 × fast-6 6) , **BH 생존 = 0** , **min raw p = 0.010**.
- top 셀: `volume+duty h20 knn` rankIC **+0.073** raw_p 0.010 → **BH_q 0.450**(미생존);
  `volume+duty h20 naive_bayes` +0.058 p0.010 q0.450; `duty h5 decision_tree` +0.054 p0.045 q0.600.
- **90개 중 raw p<0.05는 단 2개**(기대 위양성 4.5개보다 *적음*). 가장 강한 셀도 FDR q=0.45.
  → Bonferroni·BH·무보정 어느 기준에서도 생존 0 = **신호 부재(noise)**.
- ⚠️ **duty를 더하니 h20 in-sample rankIC가 올라감**(volume knn +0.029 → volume+duty knn +0.073)
  지만 유의하지 않음 = 피처를 늘리면 best-of-노이즈만 커지는 전형(과적합 징후, 신호 아님).
  → **사용자 duty 가설은 N=57·FDR에서 지지되지 않음.**

### 5.2 디스크립티브 위너 타깃 검정 (무거운 모델 보완, 확정)
fast-6에서 제외한 위너를 1모델씩 단독 permutation(permute=200, `run_perm_heavy.sh`):
| model | h | rankIC | raw p |
|---|---|---|---|
| svm_rbf | 10 | +0.038 | 0.105 |
| **svm_rbf** | **20** | **+0.050** | **0.035** |
| svm_rbf | 30 | +0.034 | 0.155 |
| grad_boost | 5 | +0.043 | 0.105 |
| random_forest | 60 | +0.038 | 0.070 |
- **svm_rbf h20 volume이 유일하게 raw p<0.05(0.035)** 이나, 인접 horizon(h10 0.105·h30 0.155)이
  명백한 노이즈라 **horizon-비견고**(전형적 노이즈 픽). 5종 포함 전 패밀리 N=95 BH-FDR로
  보면 svm h20 **BH_q 0.524** = 미생존.

### 5.3 최종 패밀리 BH-FDR (N=95, fast-6 + 무거운 위너)
- **family N = 95 , BH 생존 = 0 , min raw p = 0.010.** 95개 중 raw p<0.05는 4개(기대 위양성
  ~4.75와 동일) = 교과서적 무신호. top: volume+duty h20 knn +0.073 (q0.475)·nb +0.058 (q0.475)·
  svm h20 +0.050 (q0.524)·duty h5 dtree +0.054 (q0.524). **어느 것도 FDR 생존 못 함.**

## 6. 결론 / 함의
**채용은 미래수익 방향에 동행하되 선행(예측)하지 않는다 — 3종목 결과가 N=57·표본 ~2,200
고출력에서도 유지된다.** 전 패밀리(90셀) BH-FDR 생존 0, 최강 셀조차 q=0.45. 사용자 핵심
가설인 **duty(직무 mix)도 방향 알파를 만들지 못했다**(volume+duty가 in-sample rankIC만
키우고 유의성은 없음 = 과적합). 이는 특허·DataLab 단독 무신호와 일관
([[patent-ml-rejected]]·[[ml-bakeoff-datalab-result]]).

**다음 레버**: ① 방향예측을 접고 채용을 **나우캐스팅/근거확인**(어떤 직무를 뽑는지 = 사업
방향 컨텍스트)으로 제품화, ② 또는 **다중소스 횡단면 융합**(채용+특허+DataLab aggregator).
단일 대체데이터 단독 방향 알파는 채용까지 3소스 전부 기각 — 같은 단독 검정은 반복하지 말 것.

## 7-bis. 추가검정 (사용자 질문 2건)

### A. 공고일 이벤트 스터디 — "공고 날 바로 튀나?"
공고 observed_date ±10거래일 CAR(AR=종목−KOSPI), 57유니버스 precise 매칭, 이벤트(stock,day) 4,712건
(`scripts/hiring_event_study.py`):
| 버킷 | n | AR(0) 당일 | CAR[−10..−1] 사전 | CAR[+1..+10] 사후 | post t | vol_post/pre |
|---|---|---|---|---|---|---|
| ALL | 4712 | **−0.00%** | +0.04% | −0.11% | −1.16 | 0.987 |
| single | 4667 | −0.00% | +0.05% | −0.13% | −1.31 | 0.986 |
| burst(≥5) | 45 | +0.21% | −0.00% | +1.34% | +0.94 | 1.033 |

- **공고일은 주가에 대해 완전한 비-이벤트**: 당일 점프(AR0≈0)·사전 드리프트·사후 드리프트 모두 없음
  (post t=−1.16 무의), 변동성 증가도 없음(vol_post/pre 0.987). 개별 채용공고는 대형주에 일상적·저정보.
- 대량공고일(≥5)만 사후 +1.34%지만 **n=45·t=0.94 = 노이즈**. → "공고 날 튄다"는 통념은 데이터상 거짓.
- *(주: tech_share 중앙값이 0.0이라 기술직 버킷 분할은 degenerate — single/ALL 결과로 충분.)*

### B. 시총 하위(소형) 부분집합 재검정 — "효과가 소형에 집중되나?"
57유니버스를 시총 터사일로 분할, 최소 19종목(SMALL, 1.0~2.3조)으로 재검정.
- **디스크립티브: SMALL이 전체보다 rankIC 높음**(H5 decision_tree +0.130·H10 voting +0.073·
  H20 RF +0.052·H30/60 HGB +0.049/+0.053) — 학술적 "소형 집중" 방향과 일치해 보임. 단 표본 작고
  (n~520) sd≈mean(불안정)이라 permutation 필수.
- **permutation+BH-FDR 결과(N=34, fast-6 + 무거운 위너): 생존 0.** min raw p=0.015
  (H5 decision_tree +0.130) → **BH_q=0.510 미생존**. 무거운 위너(voting h10 p0.105·RF h20 p0.145·
  HGB h30/60 p0.145/0.205) 전부 노이즈. H5 dtree는 다른 horizon서 붕괴(h20 +0.034·h30 +0.002)
  ·sd≈mean·최단 horizon = 전형적 노이즈 픽.
- **LARGE 터사일 비교(디스크립티브)**: best rankIC도 +0.026~+0.083(H10 stacking +0.083)로
  SMALL과 동급 노이즈. → SMALL이 약간 높은 건 표본↓·횡단면 종목수↓(19개) 노이즈 증폭일 뿐,
  견고한 "소형 집중" 신호 아님.
- ⚠️ **caveat: SMALL도 1~2조원 미드캡**(KOSPI200 내). 학술 소형주효과의 micro-cap/KOSDAQ 진성
  소형이 아님 → 진성 소형 검정은 별도 소형 유니버스 시드+재스캔(~8h) 필요. 단 §A 이벤트스터디가
  공고일=완전 비-이벤트임을 보였으므로 진성 소형서도 방향 알파 가능성은 낮음.

**추가검정 종합: A(공고일 비-이벤트) + B(소형서도 FDR 무신호) → 채용 방향 알파 최종 기각.
사용자 지시대로 나우캐스팅/근거 방향으로 전환(§8).**

## 8. 피벗 — 나우캐스팅 / 근거 컨텍스트 (방향예측 대신)
방향 알파가 단독·이벤트·소형서 전부 부재 → 채용을 **"무엇을 하는 회사인가"를 설명하는 컨텍스트**로
재포지셔닝(원래 제품 기획의 흔적탐지·긍정/주의 근거 방향과 일치 [[product-vision-scoring]]):
- **직무 mix 나우캐스팅**: 종목별 기술/R&D 직군 비중·추세(이미 `duty_features`·대시보드 차트④로
  산출) → "이 회사가 지금 AI·반도체 직군을 공격적으로 늘림" = 사업 방향 *서술*(예측 아님).
- **근거 레이어**: 채용 급증/기술직 전환을 신호의 *근거 카드*로 노출(분석기 evidence, polarity=neutral).
- **다중소스 융합**만 남은 방향 검증 레버(채용+특허+DataLab aggregator 횡단면).

## 7. 한계
- **생존편향**: 현재 KOSPI200 구성으로 과거를 봄(상폐·편출 누락) — 완전 PIT 유니버스는 범위 밖.
- **커버리지 편향**: jasoseol 한정(메가캡 자사포털 과소). duty는 멀티핫(공고당 ~13직군)이라
  "넓게 뽑는다" 신호가 mix 변화를 희석할 수 있음.
- **permutation 모델 제약**: 무거운 모델은 fast-6 + 타깃 검정으로 분리(전수 permutation 아님).

## 부록 — 재현
- 코드(미커밋, worktree `sa-hiring-ml`/브랜치 `research/hiring-ml-phase45`):
  `app/ml/research/{hiring_db,hiring_dataset,bakeoff,stats}.py`,
  `scripts/{hiring_coverage_report,hiring_fdr_aggregate}.py`,
  `tests/{test_ml_stats,test_ml_hiring_duty}.py`(+기존 13) **= 25 GREEN**.
- 선별: `scripts/hiring_coverage_report.py --min-posts 30 --min-years 3`.
- 스윕: `run_sweep_volume.sh`(디스크립티브)·`run_perm_sweep.sh`(permutation).
- 집계: `hiring_fdr_aggregate.py --csv volume=perm_vol.csv duty=perm_duty.csv volume+duty=perm_both.csv`.
- 산출물: `C:\Users\804\Documents\ML\Hiring\`(coverage·vol_h*·perm_*·대시보드).
