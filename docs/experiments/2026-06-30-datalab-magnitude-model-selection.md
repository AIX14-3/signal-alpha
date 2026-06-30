# DataLab 매그니튜드 — ML 모델 선정 (회귀 bake-off, 2026-06-30)

> worktree `sa-ml-longhorizon` @ `feat/ml-datalab-longhorizon`. prod 쓰기 0. 연구(브랜치 커밋+push 백업, main 머지 보류).
> 한 줄: 검색→미래 **변동성**은 신호가 abn에 **선형**이라 ML이 단순 z-score를 못 이김(linear best, rankIC +0.15~0.19). 검색→미래 **거래량**은 **비선형 ML이 압승**(svm_rbf rankIC +0.31~0.44, GBM/RF는 rankIC +0.37~0.39 **+R²+0.09**) — 단변량 abn IC(+0.30)를 유의 상회.

## 배경 / 목적
검색 가치지형은 이미 확정: 방향=기각, **매그니튜드(미래 변동성·거래량)=반석**(비중첩 횡단면 IC +0.15~0.30, permutation2000+BH-FDR 6/6 q≈0; `2026-06-29-datalab-direction-fundamental-closeout.md`). 그 검증은 **단일 abn z-score의 IC**로만 했고 제품(attention_spike PR #628)은 z-버킷→배수 룩업. 이번 질문: **여러 ML 모델이 DataLab 피처로 미래 매그니튜드를 룩업·단변량보다 잘 예측하는가 — 그렇다면 어떤 모델인가.**

## 방법
- **데이터**: `--source` 무관 `--task magnitude` 경로. 종목명 검색 `stockname_daily_kosdaq.csv`(49종목) + 가격/거래량 `prices_kosdaq.csv`(46종목 close+volume, 2021-01-04~2023-12-28) = 검증 신호를 만든 그 데이터. prod DB DataLab은 stale(2025만)이라 미사용.
- **피처(PIT, 검색 파생 4종)**: `abn`(종목명 검색을 거래일에 ffill→직전 60거래일 rolling z; 검증된 그 예측변수), `abn_mom`(abn 1주 차분), `search_level`(현재 ffill 검색 레벨), `obs_age`(마지막 실관측 경과일). 누수 가드: 피처는 as_of 이하만, 타깃은 as_of 초과 가격만(거래량 베이스라인은 이전만).
- **타깃(연속, 비방향)**: 변동성=`forward_realized_volatility`(향후 h일 로그수익 pstdev×100), 거래량=`forward_abnormal_volume`(향후 h일 평균거래량/직전60일 평균). `app/ml/labels.MagnitudeLabel` 그대로.
- **모델 선정**: 회귀 bake-off(`build_regressor_registry`, 16종 = mean/median 베이스라인·ridge·linear·트리5·knn·SVR·GP·XGB·LGBM·CatBoost·Voting·Stacking). **walk-forward expanding 5폴드**(날짜경계 분할, 누수보호). 1순위 지표 = **Spearman rankIC(예측 vs 실현 매그니튜드)** 폴드 평균±sd. R²>0 = 평균 베이스라인(DummyRegressor) 초과. h∈{5,10,20}, 비중첩 위해 signal-step=h.

## 결과

### 변동성 (forward realized volatility) — 최적 = linear/ridge
| h | n(test) | best model | rankIC (sd) | IC | R² | 2위권 |
|---|---|---|---|---|---|---|
| 5 | 4263 | **linear/ridge** | **+0.171** (0.099) | +0.198 | +0.023 | stacking +0.152, grad_boost +0.125 |
| 10 | 2208 | **ridge/linear** | **+0.192** (0.077) | +0.215 | +0.019 | stacking +0.128 |
| 20 | 1040 | **ridge/linear** | **+0.148** (0.052) | +0.180 | −0.004 | grad_boost +0.062 |

- **선형이 전 호라이즌 1위.** 트리/부스팅은 rankIC 더 낮고 **R² 음수**(과적합) — 변동성↔abn 관계가 **선형·저차원**이라 복잡도가 해롭다.
- 단변량 abn 횡단면 IC(closeout: vol h5 +0.178)와 **거의 동일** → ML이 룩업/단변량 대비 **가치 추가 없음**. 변동성 매그니튜드는 **현행 z-버킷 룩업 유지가 정답**.

### 거래량 (forward abnormal volume) — 최적 = 비선형(svm_rbf 랭킹 / GBM·RF 레벨)
| h | n(test) | best rankIC | rankIC (sd) | R²@best | 균형 best(rankIC, R²) | linear rankIC |
|---|---|---|---|---|---|---|
| 5 | 4246 | **svm_rbf** | **+0.435** (0.050) | −0.001 | grad_boost(+0.389, **+0.094**)·rf(+0.366, +0.088) | +0.305 |
| 10 | 2197 | **svm_rbf** | **+0.401** (0.033) | −0.014 | stacking(+0.329, +0.049)·ridge(+0.270, +0.068) | +0.270 |
| 20 | 1035 | **svm_rbf** | **+0.306** (0.008) | −0.043 | stacking(+0.224, +0.062)·ridge(+0.198, +0.068) | +0.198 |

- **비선형 ML이 선형을 명확히 상회**: rankIC가 linear +0.305 → **svm_rbf +0.435**(h5)로 점프. 단변량 abn IC(closeout: volume h5 +0.299)도 유의 상회 → **거래량 타깃엔 ML이 진짜 가치 추가.**
- **svm_rbf = 랭킹 최강이나 R²≈0**(어느 종목이 거래 몰릴지 *순위*는 잘 맞히나 *배수 수준*은 못 맞힘) → **티어링(주목 등급) 용도엔 svm_rbf**. **배수 수준 추정엔 grad_boost/random_forest**(rankIC +0.37~0.39 **+R²+0.09**, 랭킹·레벨 둘 다 양호)가 균형 최적.

### 공통
- `gaussian_process`는 이 표본(n 1k~4k)서 평균만 예측(rankIC n/a, R²=베이스라인) — 무용. `baseline_mean/median`은 상수예측이라 rankIC 정의 안 됨(R²로 비교).
- 변동성 y_mean≈2.4~2.8%, 거래량 y_mean≈1.3~1.5×. 드롭=abn 히스토리 부족(~330건 h5)·forward 윈도우 부족(~46~89건).

## 결론 / 선정
- **변동성**: **linear/ridge**(=단순 abn z-score). ML·룩업 동급 → 제품은 현행 캘리브레이션 유지, 모델 추가 불필요. rankIC +0.15~0.19.
- **거래량**: **티어링=svm_rbf(rankIC +0.31~0.44), 배수추정=GBM/RF(rankIC +0.37~0.39·R²+0.09)**. 단변량/선형 대비 **유의 상회 = ML 도입 가치 있음**. 비선형(abn×레벨×recency 상호작용)이 거래량 몰림을 더 잘 포착.

## 주의 / 한계
- 단일레짐(2021~23)·KOSDAQ 46·종목명 검색 단일키워드. **신호 자체는 closeout서 permutation+BH-FDR 통과**, 본 실험은 *그 위의 모델 비교*(여기 rankIC는 폴드별 pooled test셋 IC = closeout의 per-date 횡단면 IC와 다른 잣대, 재검증 아님).
- 거래량 타깃은 일부 기계적(검색≈거래관심 근접) — closeout서 forward 견고성 확인됨. svm_rbf의 R²≈0은 스케일 미보정(랭킹 전용).
- 다음 레버: 브로드250 재수집(쿼터 후) 표본·레짐 확대 → 비선형 거래량 우위 재확인. **다중소스 매그니튜드**(채용·특허 abn도 같은 회귀 하니스로) = 별도 세션(사용자 진행).

## 산출물 (브랜치 커밋, 머지 보류)
- 하니스 회귀 경로: `app/ml/{models,evaluation,report,bakeoff,datalab_dataset,prices_csv}.py` + 신규 `app/ml/magnitude_dataset.py` + `app/ml/labels.py`(MagnitudeLabel). 테스트 `tests/test_ml_harness.py` 21 GREEN, ruff clean.
- 실행: `uv run python -m app.ml.bakeoff --task magnitude --target {volatility|volume} --prices-csv prices_kosdaq.csv --keyword-csv stockname_daily_kosdaq.csv --horizon {5|10|20} --signal-step {=h}`.

관련 메모리: [[attention-lead-lag-evidence]] · [[ml-bakeoff-datalab-result]] · [[product-vision-scoring]] · [[research-tooling-no-merge-until-signal]]
근거: `2026-06-29-datalab-direction-fundamental-closeout.md` · `docs/attention-spike-flag-design.md`
