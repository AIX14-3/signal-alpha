# DataLab ML 종합 보고서 — 무엇을 검정했고 무엇이 남았는가 (2026-07-02)

**한 줄 결론: DataLab 검색의 확정 트레이더블 가치는 "대칭 매그니튜드(미래 변동성·거래량) + 차기매출 level 나우캐스트"뿐이며, 주가 방향·꼬리위험·독립 PEAD 알파는 10개 실험·honest 검정(permutation+BH-FDR)에서 전부 기각됐다. 서비스에는 무거운 앙상블이 아니라 변동성=선형/z-룩업·거래량=svm_rbf/GBM이 비용·효율·기능 면에서 최적이다.**

> worktree `sa-ml-longhorizon` @ `feat/ml-datalab-longhorizon`. prod 읽기전용·데이터 미커밋(연구)·도구만 커밋. 이 문서는 2026-06-25~07-01 실험 10건의 종합.

## 0. 요약 (Executive Summary)

| 검정 축 | 결과 | 근거(핵심 수치) |
|---|---|---|
| 주가 **방향**(오를지/내릴지) | ❌ 기각 | 전 키워드·유니버스 NULL; 바this-bake-off Dbase≤0, rankIC≈0 |
| 미래 **변동성**(매그니튜드) | ✅ 신호 | 횡단면 IC +0.15~0.19, permutation+BH-FDR 생존 |
| 미래 **거래량**(매그니튜드) | ✅ 신호 | 횡단면 IC +0.21~0.44, BH-FDR 생존; ML(svm/GBM)이 가치 추가 |
| 차기 **매출 level**(나우캐스트) | ✅ 신호(비트레이딩) | 219종목 lag1 IC +0.051 t2.73, BH-FDR 9/10 |
| **PEAD**(발표후 드리프트) | ⚠️ 검색 알파 아님 | 드리프트는 매출 서프라이즈(SUE) 몫; 검색은 SUE 통제 시 소멸 |
| **조건부 반전**(리테일 서식지) | ❌ 기각 | 반전은 대형·유동주(가설 반대), 42셀 다중검정서 0생존 |
| **crash/꼬리위험**(NCSKEW/DUVOL) | ❌ 무신호 | KRX250 0/21, 고개인 셀 오히려 음부호 |
| **SUE-PEAD 경제성** | ⚠️ 한계적 | 20일 비용후 사망, 40일 비용 견디나 t≈1.9(<2) |

---

## 1. 데이터 범위·유니버스

| 파일 | 컬럼 | 종목수 | 기간 | 행수 |
|---|---|---|---|---|
| `prices_krx250.csv` | ticker,date,close,volume | 238 | 2016-01-04~2023-12-28 | 421,729 |
| `stockname_daily_krx250.csv` | ticker,keyword,period,ratio | 242 | 2016-01-01~2023-12-31 | 619,871 |
| `dart_krx250.csv` | ticker,name,year,reprt,account,fs_div,amount | 219 | 2015~2023 | 12,130 |
| `prices_kosdaq.csv` | ticker,date,close,volume | 46 | 2021-01-04~2023-12-28 | 31,584 |
| `stockname_daily_kosdaq.csv` | ticker,keyword,period,ratio | 49 | 2021-01-01~2023-12-31 | 51,648 |
| `prices_kospi15_2016_2026.csv` | ticker,date,close (+KS11) | 16 | 2016-01-04~2026-06-24 | 37,862 |
| `kosdaq_smallcap.json` | ticker,name,market,marcap_won | 50 | 시총 3~45%ile 밴드 | — |
| `krx_top250.json` | ticker,name,market,marcap_won | 250(190 KOSPI+60 KOSDAQ) | 시총 상위 | — |

- **데이터 소스**: 네이버 DataLab 검색어 트렌드(종목명·의도·이벤트 키워드), 가격은 FDR/pykrx, 펀더멘털은 DART(매출·영업이익), 벤치마크 KS11.
- **핵심 특징**: KRX250 = 넓은 표본(2016–23, 8년), KOSDAQ 소형주 = 짧은 표본(2021–23, 3년·46~50종목). ML 빌더는 검색·가격 CSV가 **둘 다 있는 종목만 교집합**으로 사용.

---

## 2. 사용한 모델 — 모델 경연(bake-off) 하니스

한 데이터셋에 **전 모델을 walk-forward로 돌려 표로 비교**하는 방식(`app/ml/bakeoff.py`). 특정 모델 하나를 미리 고르지 않고, "어떤 계열이 이 문제에서 이기는가"를 데이터가 답하게 함.

### 2a. 분류(방향) 레지스트리 — 16개 기본(부스터 3종 설치 시 19개)
| 계열 | 모델 | 하이퍼파라미터 | 전처리 |
|---|---|---|---|
| baseline(정직성 기준) | baseline_majority, baseline_stratified | DummyClassifier | imputed |
| 선형/확률 | logistic(max_iter=1000), ridge, lda, naive_bayes | 기본 | scaled(선형)/imputed(NB) |
| 트리 | decision_tree, random_forest(300), extra_trees(300), grad_boost, hist_grad_boost | random_state=42 | imputed |
| 부스터(옵션) | xgboost, lightgbm, catboost | n_estimators/iterations=300 | imputed |
| 거리/커널 | knn(k=15), svm_rbf, gaussian_process | — | scaled |
| 메타앙상블 | voting_soft, stacking(final=logistic,cv=3) | base=logistic+RF(200)+histGB | — |

### 2b. 회귀(매그니튜드) 레지스트리 — 14개 기본(부스터 시 17개)
lda·naive_bayes는 회귀형 없어 제외, baseline은 mean/median. ridge·linear·트리 5종·knn/svr/GPR·부스터 3종·voting/stacking(final=Ridge).

### 2c. 왜 이 모델들인가 (선정 철학)
- **계열 전수 커버**: 선형→트리→커널→앙상블까지 넣어, 신호가 **선형인지 비선형인지**를 데이터가 드러내게 함(사전 편향 배제).
- **소표본 위생**: 거리/선형/커널은 `SimpleImputer(median)+StandardScaler` 파이프라인으로 스케일 핸디캡 제거, 트리는 imputer만.
- **baseline = 정직성 가드**: DummyClassifier(다수결)를 반드시 이겨야(`Dbase>0`) 의미 있음. baseline은 표에서 항상 최하단 고정.
- **재현성**: 전 모델 `seed=42`.

---

## 3. 피처(입력) 설정

### 3a. 방향 피처 (`app/analyzers/datalab/indicators.py`, prefix `datalab__`)
관측 기간을 절반(midpoint)으로 나눠 계산: `weighted_recent_avg`, `weighted_prior_avg`, `momentum_pct`(수요키워드 모멘텀), `spike_ratio`, `avg_change_pct`, `risk_momentum_pct`(위험키워드·약세), `days_since_latest` 등.

### 3b. 매그니튜드 피처 (`app/ml/magnitude_dataset.py`, prefix `magnitude__`, WIN=60)
- **`abn`** = 검색 rolling-z(직전 60거래일, look-ahead 없음): `(level[d] − mean(hist)) / pstdev(hist)`. ← **핵심·유일 검증 피처**
- `abn_mom` = abn 현재 − 5일 전 abn, `search_level`(원 ffill 값), `obs_age`(관측 경과일).

### 3c. 검색 abnormal(PIT rolling-z) 3정의 — 주기별
| 스크립트 | 창 | 요건 |
|---|---|---|
| search_to_magnitude.rolling_z | 60 **거래일** | i≥30, hist≥30, sd>0 |
| search_target_change._abnormal | 26 **주** | hist≥13, sd>0 |
| search_to_fundamental.search_features | 4 **분기** | hist≥2, sd>0 |

모두 **point-in-time(과거만)** — 미래 정보 누수 없음.

### 3d. 매출/SUE 피처
`quarter_search`(분기 평균 검색)·`rev_yoy`(단분기 매출 YoY, de-cumul+winsor[-0.9,3.0])·`revenue_sue`(YoY를 자기 직전 4분기 평균/표준편차로 표준화 = 성장 가속도, PIT).

---

## 4. 라벨(정답) 설정

| 과제 | 라벨 | 정의 |
|---|---|---|
| 방향 | y_direction | 초과수익(vs KS11) `|excess|<=0.3%`는 중립밴드로 드롭, 아니면 1/0 |
| 변동성(매그) | fwd_vol | 향후 h일 일별 로그수익 pstdev ×100 |
| 거래량(매그) | fwd_volume | 향후 h일 평균거래량 / 직전 60일 평균 |
| 매출 | rev_yoy / SUE | 단분기 매출 YoY, SUE=성장가속 표준화 |
| PEAD | fwd_excess | 실적발표 후 [d+1, d+21] KS11 초과수익 |
| crash | NCSKEW·DUVOL·CRASH | firm-specific 주간수익(KS11 시장모델 잔차) 왜도/하방변동/3.09σ 폭락 |
| 반전 | xs_excess | 향후 h일 횡단면 초과수익(셀 내 demean), 반전=음 |

- 방향은 **분류**(중립밴드로 애매구간 제거), 매그니튜드/매출/드리프트는 **회귀·상관**.
- horizon: 방향/매그 5·10·20거래일, 매출 분기, PEAD 20/40/60일, crash 13/26주 블록.

---

## 5. 점수 산출 방식 — 근거 matrix (★핵심)

"모델이 학습해서 낸 결론"이 어떻게 숫자로 판정됐는지. 두 층: **(A) 모델 스킬 점수**(bake-off)와 **(B) honest 유의성 검정**(permutation+FDR).

### 5a. 검증 구조 — walk-forward(누수 없는 시계열 CV)
`walk_forward_folds(dates, n_folds=5)`: 고유 날짜를 6청크로 자르고, fold i는 **청크 0..i-1로 학습 → 청크 i로 테스트**(확장창). 같은 날짜가 학습/테스트에 걸치지 않게 **날짜 경계**로 분할 → same-day 누수 차단.

### 5b. 모델 스킬 점수 (fold별 계산 → 평균±표준편차 집계)
- **bullish_score**: `predict_proba(1)` → 없으면 `decision_function` → 없으면 `predict`. 모든 모델이 랭킹 가능한 점수를 냄.
- **IC (Pearson)** = corr(bullish_score, 실현 초과수익). "점수가 수익을 얼마나 추종하나."
- **Rank-IC (Spearman)** = 순위상관 = **주 선정지표**(이상치에 강건).
- **decile_spread** = 점수 상위10% 평균수익 − 하위10% 평균수익(경제적 크기).
- **방향 정확도/F1/ROC-AUC** + **Dbase = 정확도 − 다수결baseline 정확도**(>0이어야 코인플립 이김 = 정직성 가드).
- **회귀 R²**(>0이어야 평균예측 이김)·MAE·RMSE.
- **집계**: `ModelReport.summary()`가 fold들의 **평균 ± 표준편차** — 운 좋은 단일 fold가 스킬로 위장 못 하게. `sd_*`가 작을수록 신뢰.

### 5c. honest 유의성 (소표본 횡단면 신호용)
분석적 t는 소표본서 노이즈를 과소평가(6/29 교훈) → **permutation 기본**.
- **permutation**: 타깃을 **그룹 내(같은 날짜/분기/블록) 셔플** NPERM=2000회 → 관측 IC가 귀무분포서 얼마나 극단인지 p 산출. p-floor = 1/(N+1)=0.0005.
- **BH-FDR**: 여러 셀(라벨×horizon×조건) 동시검정의 위양성 통제. Bonferroni(독립가정)보다 상관검정군에 적합.
- **within-firm 분해**: abn·타깃을 **종목별 demean** 후 재계산 → "정적 특성(종목 정체성)"인지 "시점 timing(트레이더블)"인지 가름.

### 5d. 판정 근거 matrix
| 지표 | 의미 | 신호 판정 문턱 |
|---|---|---|
| Dbase / R² | baseline 초과 | >0 (아니면 무의미) |
| Rank-IC(평균) | 점수↔수익 순위추종 | >0 & fold 일관 |
| perm_p | 우연 아님 | <0.05 & p-floor 근처 |
| BH_q | 다중검정 생존 | ≤0.05 |
| within-firm IC | timing vs 정적 | 부호·크기 유지되어야 트레이더블 |
| 분기 t / net_t(비용후) | 경제적 유의 | >2 (엄격히 ≥3, Deflated-Sharpe) |
| era 분할 | 레짐 견고성 | 양쪽 기간 동부호 |

**핵심 원리**: 하나라도 실패하면 신호 아님. 방향은 Dbase≤0·rankIC≈0서 탈락, 매그니튜드는 IC>0+BH생존+era일관 전부 통과, 매출은 BH 9/10 통과했으나 within-firm서 횡단면(또래대비)임이 드러남, SUE-PEAD는 비용후 net_t<2로 미달.

---

## 6. 실험별 결과 (연대기, 10건)

| # | 날짜 | 검정 | 세팅 | 헤드라인 수치 | 판정 |
|---|---|---|---|---|---|
| 1 | 06-25 | 시대별 키워드 방향 | 3종목·117특허키워드·16모델 | median rankIC≈0(±0.04) | NULL |
| 2 | 06-25 | 발행지연/선행성 | 24이벤트 일별 이벤트스터디 | day0 z=+1.29, lag+1 corr −0.049 | NULL(동시확인재) |
| 3 | 06-25 | 타깃변경 변동성/거래량 | abn(26주)→vol/volume | 1wk vol +0.26, volume +0.25, dir +0.005 | 매그니튜드 |
| 4 | 06-26 | 후속 계획 | (계획문서) | — | 어젠다 |
| 5 | 06-29 | 방향종결+매출나우캐스트 | KOSDAQ45/39·perm+BH | 방향 NULL; 매출 R2 perm p0.065 **BH 0/10**; 매그 **6/6 생존** | 방향NULL·매그신호 |
| 6 | 06-30 | 방향 PIT 이벤트재검정 | 21종목·수요/DART키워드 | 전 IC t<0.6, bake-off Δbase≤0 | NULL(갭종결) |
| 7 | 06-30 | 매그니튜드 모델선정 | KOSDAQ 회귀 bake-off | vol=linear +0.15~0.19; volume=svm +0.31~0.44 | 모델선정 |
| 8 | 06-30 | 매출재검정@219+PEAD | 219종목·perm+BH | 매출 lag1 +0.051 t2.73 **BH 9/10**; 검색→주가 q0.69 NULL; **SUE→드리프트 decile +2.24%** | 나우캐스트·PEAD=SUE |
| 9 | 07-01 | 조건부 반전 | 시총×유동성×개인 tercile | krx250 반전은 **liquid셀**(t−2.9)·42셀서 0생존 | NULL |
| 10 | 07-01 | crash+SUE경제성 | NCSKEW/DUVOL·비용스윕 | crash 0/21; SUE 20일 net_t0.25·40일 t1.91 | 무신호·한계 |

---

## 7. 모델 선정 이유·근거 (bake-off가 답한 것)

- **방향**: 어떤 모델도 baseline을 못 이김(Dbase≤0), rankIC≈0. 선형·트리·커널·앙상블·부스터 전부 실패 → **방향은 학습 가능한 구조가 없음**(효율적 시장 + 검색=동시/후행 지표).
- **변동성**: **선형/ridge가 매 horizon 최고**(rankIC +0.148~0.192), 트리/부스팅은 R²<0(과적합). ML이 단순 z-룩업 대비 가치 없음 → **선형(=z-버킷) 채택**.
- **거래량**: **비선형 우세** — svm_rbf rankIC +0.306~0.435, GBM/RF +0.366~0.389에 R²+0.09(균형) vs 선형 +0.198~0.305 → **랭킹=svm_rbf, level추정=GBM/RF**. 여기선 ML이 실제 가치 추가.
- **근거 원리**: rankIC(순위추종) + R²(baseline초과) + fold sd(안정성)로 판정. 거래량만 비선형 이득이 통계적으로 확인됨.

---

## 8. 우리 서비스에 맞는 모델 선정 (비용·효율·기능성)

**서비스 맥락**: 제품은 방향 예측이 아니라 **비방향 `attention_spike` 흔적 플래그**(검색 급증 시 "뭔가 일어나는 중" 표시) + 배수표(변동성·거래량 tier). 즉 필요한 건 **매그니튜드 tier**이지 방향 알파가 아님.

| 용도 | 권장 모델 | 비용 | 효율 | 기능성 | 근거 |
|---|---|---|---|---|---|
| 변동성 tier | **ridge/선형 = z-버킷 룩업** | 매우 낮음(CPU·무학습 가능) | 실시간·결정론적 | 해석가능·안정 | 트리가 R²<0 과적합, ML 이득 없음 |
| 거래량 tier | **GBM/RF(level) 또는 svm_rbf(rank)** | 중간(CPU 300트리) | 배치 재학습 주간 | rankIC·R² 이득 확인 | 유일하게 비선형이 통계적 우위 |
| 방향 시그널 | **배포 금지** | — | — | — | 전 모델 baseline 미달 |
| 매출 나우캐스트 | **리서치/주의 레이어(비트레이딩)** | 낮음 | 분기 | level 참고용 | 219종목 robust하나 주가 예측 아님 |

**배제 권고 — 부스터(xgb/lgbm/catboost)·메타앙상블(voting/stacking)**: rankIC 이득이 ridge/GBM 대비 미미한데 **의존성·빌드·컴퓨트 비용↑, 해석성↓**. 소표본·저지연·설명가능성이 중요한 이 서비스엔 부적합.

**운영 함의**:
- 피처는 **단일 abn z-score**(60일 rolling-z) — 계산 초경량, 종목당 O(1), 실시간 가능.
- 변동성 플래그는 무학습 z-버킷(현 배수표 broad-250: vol 1.13/1.21/1.35)으로 충분 — 재학습 불필요.
- 거래량 tier를 제품화한다면 GBM/RF를 **주간 배치 재학습**(GPU 불요, 수십초). 그 외 실시간 경로엔 모델 없이 z-룩업.
- 결론: **가장 싸고 해석가능한 선형/z-룩업이 대부분을 커버**, 거래량에서만 경량 트리앙상블이 정당화됨. 무거운 SOTA는 비용 대비 무의미.

---

## 9. 결론 & 남은 레버

**확정 가치지형(불변)**: DataLab = ① 대칭 매그니튜드(미래 변동성·거래량) 흔적 탐지기 + ② 차기매출 level 나우캐스터. 트레이더블 **방향/꼬리위험/독립 PEAD 알파 없음**.

**남은 레버(다음 세션, 낮은 기대)**:
- SUE-PEAD 40일 신호(t≈1.9)를 **KOSPI200 확대**나 **컨센서스 기반 SUE**로 t>2 확정 시도.
- crash는 소형 유니버스 **수백 종목 확대** 시에만 재검 가치(현 KOSDAQ 46·3년은 검정력 부족).
- 채용·특허와 **다중소스 융합**(단일 소스는 전부 소진).

**방법론 교훈(누적)**: ① 소표본 횡단면 IC는 분석적 t 신뢰 금지 → permutation 기본, ② 다중 trial은 BH-FDR + Deflated-Sharpe(t≥3), ③ 횡단면 IC 생존해도 within-firm 분해로 정적특성 vs timing 가름 필수, ④ 경제성은 반드시 거래비용 반영.
