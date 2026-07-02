# 실험 보고서 — 특허 활동 → 차기 실현변동성(매그니튜드) 신호

**일자**: 2026-06-30 · **분기**: `research/patent-magnitude-revenue-fusion` · **데이터**: 로컬 docker DB(실특허 82,869건) + FDR 주가 + DART 실매출

---

## 1. 한 줄 결론

특허 단독 **방향성**(오른다/내린다) 알파는 기각됐으나, 라벨을 **차기 실현변동성(움직임의 *크기*)**으로 바꾸자 **다중검정 보정(BH-FDR)을 통과하는 신호**가 나왔다 — 13개 KOSPI R&D 종목, 2021-2023, **12/12 모델 생존(q≤0.04, permutation p≈0)**. 단, *방향 알파가 아니라* 변동성/리스크용 신호다.

---

## 2. 어떤 신호인가 (정확히)

- **타깃(예측 대상)**: 각 종목의 **차기 20거래일 실현변동성**(일간수익률의 표본표준편차). 부호(방향) 아님, *크기*.
- **라벨화**: 같은 날짜의 종목들을 변동성으로 정렬해 **상위 절반=1(고변동)·하위 절반=0(저변동)** 로 이진화(횡단면 상대 라벨 → 미래정보 누수 없음). 연속 변동성 값은 별도 보관해 `rankIC_xs` 채점에 사용.
- **신호의 내용**: "**상대적으로 특허 활동이 활발한 종목이 이후 더 크게 움직인다**"는 횡단면 패턴. 대형주=저변동이라는 사이즈 효과와 반대 부호(+) → 사이즈 교란이 아님.
- **세 타깃 비교**:
| 타깃 | 최고 rankIC_xs | 보정 후 | 판정 |
| --- | --- | --- | --- |
| **realized_vol**(실현변동성) | **+0.29** | **12/12 BH 생존** | ✅ 신호 |
| abs_return(\ | 초과수익\ | ) | +0.18 |
| patent_revenue(차기매출 성장) | +0.08 ~ −0.19(부호 엇갈림) | — | ❌ 무신호 |

---

## 3. 피처는 어떻게 정했나

직접 고른 게 아니라 **운영 분석기와 동일한 함수**(`app/analyzers/patent/indicators.compute_indicators`)가 내는 11개 지표를 그대로 썼다(연구용으로 임의 선별 X → 과적합·체리피킹 방지). 각 신호일 `as_of`에서 **publication_date 기준 lookback 180일 윈도**의 특허만 사용(누수가드: 특허는 출원 후 ~18개월 비밀 → 공개일에만 "알 수 있음"). 윈도를 중점(midpoint)으로 갈라 recent/prior 모멘텀 계산.

| # | 피처 | 정의 | 유형 |
| --- | --- | --- | --- |
| 1 | `total` | 윈도 내 총 특허 수 | count |
| 2 | `recent_count` | 중점 이후(최근 절반) 공개 수 | count |
| 3 | `prior_count` | 중점 이전(이전 절반) 공개 수 | count |
| 4 | `momentum_ratio` | (recent−prior)/prior = 활동 **가속도** | ratio |
| 5 | `new_category_count` | 그 종목에 **신규 기술분류** 특허 수 | count |
| 6 | `new_category_ratio` | new_category_count / total | ratio |
| 7 | `distinct_tech_categories` | IPC 기술분류 **다양성** | count |
| 8 | `days_since_latest` | 최근 공개 이후 경과일(**최신성**) | count |
| 9 | `llm_enriched_count` | LLM 중요도 부여된 특허 수 | count |
| 10 | `mean_significance` | LLM 중요도 평균 | score |
| 11 | `max_significance` | LLM 중요도 최대 | score |

- **사이즈 교란 제거**: count형 피처(1·2·3·7·8·5·9)는 날짜별 **횡단면 rank 정규화**(`--xs-normalize rank`, [0,1] 백분위)로 "삼성=대형주" 같은 절대수준을 제거 → 모델이 종목 정체성이 아닌 *상대적 활동*만 보게 함. ratio/score형은 원본 유지.
- ⚠️ 9·10·11(LLM)은 이번엔 enrich를 안 돌려 대부분 비어있음(중앙값 대치) → **실효 신호는 count·momentum·category 피처에서 나온 것**.

---

## 4. 어떤 모델을 썼나 (16종)

모든 모델은 sklearn `Pipeline`. 거리/선형/커널 계열은 **중앙값 대치 + 표준화**(`_scaled`), 트리/NB/baseline은 **중앙값 대치만**(`_imputed`) — 소표본 공정성 위생.

| 묶음 | 모델 | 전처리 |
| --- | --- | --- |
| **Baseline(2)** | baseline_majority(최빈값), baseline_stratified(층화무작위) | imputed |
| **선형/확률(4)** | logistic, ridge, lda, naive_bayes | scaled(앞 3)/imputed(NB) |
| **트리/앙상블(5)** | decision_tree, random_forest, extra_trees, grad_boost, hist_grad_boost | imputed |
| **거리/커널(3)** | knn, svm_rbf, gaussian_process | scaled |
| **메타앙상블(2)** | voting_soft, stacking (logistic+random_forest+hist_grad_boost 위) | — |

> baseline은 "찍기"의 성능 — 이걸 못 이기면 학습한 게 없는 것. 선택적 부스터(xgboost/lightgbm/catboost)는 미설치라 제외(있으면 자동 합류).

---

## 5. 어떤 테스트로 검증했나 (방법론)

1. **워크포워드 교차검증(누수 0)**: distinct 날짜를 6개 연속 블록으로, fold i는 0..i-1 학습·i 테스트(과거→미래 순서, 같은 날짜가 train·test 양쪽에 안 걸침). 5 folds.
1. **4묶음 지표**로 각 모델 채점(아래 6장 matrix).
1. **permutation 검정(300회)**: 정답 라벨을 무작위로 섞어 `rankIC_xs`의 **귀무분포** 생성 → 단측 p = (귀무 ≥ 관측)의 비율. "우연이면 이 정도 나오나"를 직접 측정.
1. **BH-FDR(Benjamini-Hochberg) 다중검정 보정**: 14개 모델을 동시에 보면 하나는 운으로 좋아 보일 수 있음 → p값들을 보정한 **q값**으로, q≤0.05면 "생존(진짜)".

---

## 6. 테스트 기준(지표) Matrix

| 지표 | 묶음 | 측정 내용 | 통과 조건 | **realized_vol 결과** |
| --- | --- | --- | --- | --- |
| `acc` | A. 방향 | 분류 정확도 | > 0.5 | 0.53 ~ 0.59 |
| `Dbase` | A. 방향 | acc − 다수결 baseline | **> 0** | +0.03 ~ **+0.09** |
| `f1` | A. 방향 | 정밀도·재현율 균형 | 높을수록 | 0.46 ~ 0.61 |
| `roc_auc` | A. 방향 | 랭킹 품질(AUC) | > 0.5 | 0.57 ~ 0.65 |
| `IC` | B. 크기 | Pearson(모델점수, 실제변동성) | > 0 | +0.12 ~ +0.29 |
| `rankIC` | B. 크기 | Spearman 순위상관 | > 0 | +0.11 ~ +0.27 |
| **`rankIC_xs`** | **B. 크기(핵심)** | **날짜별 횡단면 rank-IC 평균** | **> 0 · 일관** | **+0.15 ~ +0.29** |
| `sd_xs` | D. 견고성 | rankIC_xs의 fold간 표준편차 | 작을수록 | 0.07 ~ 0.24 |
| `dec_sprd` | C. 경제성 | 상위10%−하위10% 실제변동성 차 | > 0 | +0.5 ~ +0.8 |
| `permutation p` | 검정 | P(귀무 ≥ 관측) | < 0.05(보정 전) | **≈ 0.000** (0/300) |
| `BH_q` | 검정(다중보정) | BH 보정 q값 | **≤ 0.05** | **0.000 ~ 0.039 → 12/12 생존** |

> **판정 규칙**: `Dbase>0` + `rankIC_xs>0 일관` + `permutation/BH 통과` + `sd_xs 작음` + `dec_sprd>0` 를 모두 만족해야 "진짜 실력". realized_vol은 전부 충족.

### permutation/BH 원표 (realized_vol, n_perm=300)

```plain text
               model  obs_rankIC  null_mean  p-value  BH_q  BH?
         naive_bayes      +0.287     -0.002    0.000  0.000 YES
          grad_boost      +0.234     -0.001    0.000  0.000 YES
         extra_trees      +0.225     +0.002    0.000  0.000 YES
            logistic      +0.202     +0.001    0.000  0.000 YES
               ridge      +0.196     +0.001    0.000  0.000 YES
            stacking      +0.192     +0.000    0.000  0.000 YES
         voting_soft      +0.184     +0.001    0.000  0.000 YES
         random_forest    +0.181     -0.005    0.000  0.000 YES
                 lda      +0.179     +0.000    0.000  0.000 YES
       decision_tree      +0.166     -0.005    0.000  0.000 YES
                 knn      +0.152     -0.005    0.003  0.004 YES
     hist_grad_boost      +0.098     -0.000    0.033  0.039 YES
  baseline_stratified     -0.044     +0.000    0.790  0.851  (기각 안 됨=정상)
```

---

## 7. 한계 & 다음 단계 (과대해석 금지)

- **단일 레짐**: 2021-2023 36개월 한 구간. 다른 시기 유지 미확인.
- **permutation 해상도**: 300회라 최소 p≈0.003 → Bonferroni(0.05/42=0.0012)급은 미확인(BH는 명확 통과). 확정엔 perm 1000+.
- **단일 설정**: horizon 20·lookback 180·월별. 다설정 로버스트니스 필요.
- **방향 알파 아님**: 변동성·리스크 사이징·옵션엔 유효, 방향 베팅엔 불가.
- **다음**: ①확정 replication(perm↑·다horizon·종목 확대) → ②research→main PR(신호 트리거) → ③융합(특허+채용+검색)으로 더 강한 신호 탐색.
## 8. 재현 명령

```plain text
DATABASE_URL=<local> uv run --extra dev --with scikit-learn --with scipy \
  python -m app.ml.research.bakeoff --source patent-db --target realized_vol \
  --tickers 005930,066570,005380,051910,012330,000660,204320,096770,035420,035720,068270,042700,000100 \
  --start 2021-01-01 --end 2023-12-31 --prices-csv prices14.csv --benchmark KS11 \
  --xs-normalize rank --min-cross-section 6 --lookback 180 --horizon 20 --signal-step 20 \
  --folds 5 --permute 300 --permute-exclude gaussian_process,svm_rbf
```
