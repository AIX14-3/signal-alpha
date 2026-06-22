# 메타러너 학습 규율 — 과적합 가드

`app/ml/meta_learner.py`(런타임)는 추론만 한다 — stacking 가중을 `artifacts/meta_learner.json`
에서 로드하고, 없으면 균등 폴백한다. 가중을 **산출하는 오프라인 학습**(현재 `tools/vol_benchmark`
/ harness 테스트베드)이 지켜야 할 과적합 방지 규율을 여기 고정한다. 핵심 원칙은
[worker-redesign.md](worker-redesign.md)의 "판정은 결정론·ML·게이트, LLM은 설명만".

## 배경: "행 수"가 아니라 "피처화"가 과적합을 만든다

네이버 데이터랩 원본은 종목·키워드·일자 단위로 수만 행(예: 삼성·하이닉스 키워드 23,754행)이지만,
이는 **모델 입력이 아니다**. `analyzers/datalab/rules.py:evaluate_indicators`가 이미
`(종목, as_of)당 score ∈ [-1, 1]` **하나**로 집계한다. 즉 모델이 보는 건 종목·일자당 소수의
결정론 신호다. 과적합은 원본 행 수가 아니라 아래에서 생긴다.

## 규율

1. **피처는 집계 지표 소수만.** 키워드별/청크별로 펼치지 않는다.
   - DataLab: 모멘텀·스파이크·리스크모멘텀·lead-lag 등 상대·스케일프리 지표(`compute_indicators`).
   - DART 공시: 1024차원 임베딩 raw 금지 → `dart_document_features.mean_prior_distance`
     같은 **스칼라 파생 피처**로 환원(020/021 마이그레이션, `embedding_features.mean_vector`).
   - 표본(라벨된 종목-일) 대비 피처 수를 작게 유지한다.

2. **시간 기반 walk-forward OOF만.** 랜덤 k-fold/셔플 금지(시계열 누수).
   - 베이스 모델은 이미 walk-forward(`tools/vol_benchmark/common/harness.py`).
   - 메타러너 stacking 가중도 **OOF 예측 위에서** 시간순으로 학습/검증한다.
   - `as_of ≤ t` 데이터만 t 시점 피처에 사용(룩어헤드 차단; 분석기들은 이미 clock-free).

3. **정규화·용량 제한.** L1/L2, 얕은 트리(max_depth↓), min_samples_leaf↑, early stopping.
   표본이 적을수록 단순 모델을 택한다.

4. **train↔OOF 격차 점검.** 학습 점수와 OOF 점수 차가 크면 과적합 신호 → 피처 축소/정규화 강화.
   가중 집중도(한 모델이 가중 대부분 차지)도 경계.

## 런타임 가드 (tracked)

`load_weights`는 비유한값(NaN/inf)·음수/0 가중을 제외한다 — 깨진/과적합 산출물이 추론을
지배하지 못하게 하는 최소 방어선. 산출물이 없거나 비면 균등 폴백(결정론).
