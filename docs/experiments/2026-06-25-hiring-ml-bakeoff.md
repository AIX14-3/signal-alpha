# 채용(HIRING) 단독 ML 주가방향 예측력 테스트 (2026-06-25)

> 적재한 자소설닷컴 채용 공고(삼성전자·SK하이닉스·NAVER, 2016~2023)를 **단독**으로
> 미래 주가 방향(초과수익 부호)을 예측할 수 있는지 검증. 기존 DataLab ML 바이크오프
> 하니스(`app/ml/research/`)를 재사용하고 채용 어댑터(`hiring_dataset.py`·`hiring_db.py`)만
> 얇게 추가했다.
>
> **한 줄 결론: 3종목 채용 단독으로는 어떤 horizon에서도 견고한 예측 신호가 없다.
> h=60·h=5에서 p≈0.05가 나오지만, 다중검정(5 horizon×14모델, horizon별 best 선택)·소표본·
> 폴드 불안정(rankIC 표준편차 ≈ 평균)을 감안하면 우연(위양성)으로 본다. DataLab(더 조밀)도
> 같은 3종목에서 신호 0이었던 것과 일관.**

---

## 1. 실행 환경
| 항목 | 값 |
|---|---|
| 실행 | 로컬 PC(Windows), repo `.venv`(uv) — 고전 ML, 연산 초소형 |
| DB | Supabase prod(`zltlpcpmdooosekipgsd`) — **read-only** |
| 가격 | FinanceDataReader → `prices_hiring.csv`(005930·000660·035420·KS11, 2016-01~2024-06, 2087세션/종목) |
| 하니스 | `app/ml/research/`(DataLab과 공용) + 신규 `hiring_dataset.py`·`hiring_db.py`, `--source hiring-db` |

## 2. 데이터: 유니버스 · 표본 · 계절성
- **유니버스(3종목)**: 삼성전자·SK하이닉스·NAVER. 삼성·하이닉스는 반도체 동조 → 독립표본 사실상 <3.
- **채용 공고수(2016~2023, jasoseol)**: NAVER 181 · SK하이닉스 105 · **삼성전자 44** = 합 ~330건.
  → ML엔 매우 작고, 채용은 연속 시계열이 아니라 **이벤트성(띄엄띄엄)**.
- **계절성(실측)**: 분기 Q1 94·Q3 103 高 / Q2 62·Q4 71 低, 월별 **3월 41·9월 43 피크**(한국 상·하반기
  정기공채). 원시 카운트는 "달력"을 인코딩 → **계절 보정 필수**.

## 3. 방법론
- **타깃**: h일 선도 **초과수익**(종목−KOSPI) **부호**(분류), neutral band 0.3%(미세변동 드롭).
- **horizon 스윕**: 5·10·20·30·60 거래일.
- **신호일**: 거래일 기준 **20일(≈월간) 간격** — 겹치는 h일 라벨 자기상관(누수성) 완화.
- **피처(소수 핵심 3개, 전부 point-in-time = `published_at` KST 기준)**:
  - `hiring__deseason_momentum` — 윈도(90일) 후반/전반 **계절보정** 공고 플로우 모멘텀
  - `hiring__yoy_change` — 동일 윈도 전년 대비 변화
  - `hiring__days_since_latest` — 최근 공고까지 경과일
  - **계절지수**는 우리 8년 공고로 직접 산출(월별 share/uniform, 풀링) — DataLab의 NAVER검색
    기반 `hiring_baseline`(종목별 미적재 가능)에 의존하지 않음.
- **CV**: 확장창 워크포워드(`walk_forward_folds`, 날짜경계·같은날 누수차단), folds=5, seed=42.
- **모델**: baselines(majority/stratified) + logistic·ridge·lda·NB·tree·RF·ET·GB·HGB·knn·svm·GP·voting·stacking(16종).
- **지표**: accuracy·AUC·IC·**rankIC**·decile_spread(폴드 mean±std) + **퍼뮤테이션 p값**.

## 4. 결과
표본: horizon별 **127~134건**(min_obs=2, lookback 90, 월간신호 → too_few_observations로 162건 드롭).

### 4.1 horizon별 최고(rankIC 기준) vs 베이스라인
| h | best model | acc | Δbase | rankIC | sd(rankIC) | base acc | rankIC>0 모델수 |
|---|---|---|---|---|---|---|---|
| 5 | logistic | 0.492 | +0.017 | +0.168 | 0.155 | 0.475 | 10/14 |
| 10 | naive_bayes | 0.548 | +0.046 | +0.001 | 0.250 | 0.502 | 1/14 |
| 20 | random_forest* | 0.603 | +0.062 | +0.015 | 0.228 | 0.540 | — |
| 30 | hist_grad_boost | 0.488 | −0.032 | +0.049 | 0.185 | 0.520 | 3/14 |
| 60 | decision_tree | 0.536 | +0.031 | +0.158 | 0.160 | 0.506 | 13/14 |

(*h=20은 acc 최고 RF; rankIC 최고와 별개) **모든 행에서 sd(rankIC) ≈ rankIC** → 폴드 간 불안정.
rankIC>0 모델수가 horizon마다 10→1→3→13으로 **부호 일관성 없음** = 노이즈 특성.

### 4.2 퍼뮤테이션 검정(라벨 셔플 ×300, 단측 p)
| 구성 | n | obs rankIC | null mean | null p95 | p-value | 판정 |
|---|---|---|---|---|---|---|
| h=5 logistic | 127 | +0.168 | −0.001 | +0.167 | **0.050** | 경계(노이즈) |
| h=60 decision_tree | 134 | +0.158 | +0.008 | +0.156 | **0.047** | 경계 |
| h=20 random_forest | 128 | +0.015 | −0.003 | +0.163 | 0.420 | 노이즈 |

**다중검정 보정**: 5 horizon × 14 모델 ≈ 70개 조합에서 horizon별 best를 골라 검정 → 선택편향.
기대 위양성 ≈ 70×0.05 ≈ 3.5개. Bonferroni 임계 0.05/70 ≈ 0.0007 → **p≈0.05 둘 다 탈락**.
→ **유의 신호 아님.**

## 5. 채용 데이터 가공 시 고려사항(이번에 반영/점검한 것)
1. **계절성** — 자체 월별 계절지수로 deseason(모멘텀·YoY). 원시 카운트 직접 투입 금지.
2. **희소성·럼피함** — lookback 90일·월간 신호·min_obs 게이트(부족분 **드롭**, 날조 없음).
3. **Point-in-time** — `published_at`(KST) ≤ as_of 공고만; 라벨은 as_of 이후 가격만.
4. **종목 간 정규화** — 절대수 대신 모멘텀/YoY(자기 기준 변화율)로 삼성44 vs NAVER181 비교가능.
5. **커버리지 편향** — jasoseol 한정; 삼성 자사포털 위주라 과소계상 → 삼성 해석 보수적.
6. **겹치는 horizon 누수** — 신호일 ≥월간으로 라벨 자기상관 완화(하니스 워크포워드는 같은날만 차단).
7. **소표본 통계** — 피처 3개·단순/규제 모델·베이스라인 must-beat·퍼뮤테이션·다중검정 보정.

## 6. 한계 / 다음 레버
- **핵심 제약 = 통계력**: 3종목(독립<3)·~330공고·이벤트성 → 신호 검출력이 근본적으로 낮음.
- **다음 레버**: (a) **유니버스 확장**(비반도체 다수 종목 채용 backfill + 가격) → 패널 N↑,
  (b) **다중소스 융합**(채용+DataLab+특허), (c) OCR 기술수요 피처(현재 prod 미적용),
  (d) closing_date 기반 "동시 오픈 공고수"(스톡) 피처.

## 부록 — 산출물 / 재현
- 코드(미커밋, app/ml 실험과 동일 취급): `app/ml/research/hiring_dataset.py`·`hiring_db.py`,
  `bakeoff.py`(`--source hiring-db`), `tests/test_ml_hiring_dataset.py`(8 GREEN).
- 재현: `uv run python -m app.ml.research.bakeoff --source hiring-db --tickers 005930,000660,035420
  --start 2016-01-01 --end 2023-12-31 --benchmark KS11 --prices-csv prices_hiring.csv
  --lookback 90 --horizon {5..60} --signal-step 20 --min-obs 2`
- 결과 CSV: `hiring_h{5,10,30,60}.csv`. 퍼뮤테이션: `_perm_hiring.py`(throwaway).
