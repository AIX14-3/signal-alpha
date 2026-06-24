# DataLab 단독 ML 재시험 — 장기 horizon (1d~2y), 10년 데이터

**날짜:** 2026-06-24 · **브랜치:** `feat/ml-datalab-longhorizon` · **상태:** ⏸ 파일럿 완료 / 본 백필 대기

## 1. 배경 & 목적

[2026-06-23 실험](2026-06-23-datalab-ml-bakeoff.md)에서 DataLab 단독(15종목·2021~2023·horizon
5~60일)으로 "신호 없음(확정)" 결론. 두 한계를 보완해 **더 공정하게 한 번 더** 검증한다:

1. **기간 확장** — 3년 → Naver 일별 최대치 **2016년~** (장기 horizon은 표본 겹침 때문에
   3년으로는 독립 관찰이 종목당 1~3개뿐).
2. **느린 신호 탐색** — 검색 트렌드의 주가 영향이 분기·반기·연 단위로 *느리게* 올 수 있는데
   어제는 60일까지만 봤다. 이번엔 **1일~2년**으로 쪼개 "어느 시간대에 영향이 있나"를 본다.

종목은 15개 유지(이미 매핑·부분 수집됨), DataLab 단독(특허 융합은 다음 트랙).

## 2. 설계

| 항목 | 값 |
|---|---|
| 종목 | 15 (`000100,000270,000660,005380,005930,035420,035720,041510,042700,068270,204320,207940,253450,259960,352820`) |
| 기간 | 2016-01-01 ~ 2026-06-24 |
| horizon 그리드 | `1, 5, 21, 63, 126, 252, 504` 거래일 = 1일·1주·1달·3달·6달·1년·2년 |
| signal_step | `max(5, horizon)` — 긴 horizon은 신호창 비겹침 |
| 타깃 | 미래 초과수익률(벤치마크 KS11 대비), neutral band ±0.3% |
| 벤치마크/가격 | KS11, 로컬 CSV(FDR) — `ohlcv_data` 미사용 |

### 핵심 방법론 — "긴 horizon = 가짜 신호" 함정 차단
긴 horizon은 각 샘플의 결과 관찰창이 겹쳐 rankIC가 **가짜로 부풀 수 있다**(어제 2021 h=10
+0.27이 3년치서 소멸한 게 이 함정). 하니스는 이를 자동 보정하지 않으므로, **신호 간격(`--signal-step`)을
horizon에 맞춰 늘려 비겹침**으로 만들고 **독립 표본 수를 metric과 함께 본다**.

## 3. 코드 변경 (이 브랜치)

- `app/ml/bakeoff.py` — `--signal-step` CLI 추가(기본 5=주간, 긴 horizon엔 horizon에 맞춤).
  배선은 `load_from_env → load_datalab_dataset(signal_step=) → weekly_signal_dates(step=)`로 이미 존재.
- `app/ml/evaluation.py` — **견고성 수정**: `evaluate_model`의 fit/predict를 fold 단위 try/except로
  감쌈. 소표본 폴드에서 한 모델(예: KNN n_neighbors>학습표본)이 실패해도 run 전체가 죽지 않고
  해당 모델·폴드만 건너뜀(빈 폴드는 summary에서 nan). 회귀 테스트 추가.
- `scripts/sweep_datalab_horizons.py` — horizon 그리드를 루프(각 horizon에 맞는 signal_step)하며
  horizon별 표본수·최고 rankIC 통합표 출력.
- `scripts/backfill_prices_fdr.py` 재사용 — 2016~2026 가격 CSV 생성(`prices_kospi15_2016_2026.csv`, untracked).

## 4. 읽기전용 파일럿 (2021~2023 기존 prod DataLab, 쓰기 0)

본 백필 전 파이프라인을 실데이터로 검증 + 장기 horizon 미리보기. 넓은 가격 CSV로 forward 라벨 확보.

```
 horizon  samples        best_model   rankIC   sd_IC
   1(1d)     1636          catboost   +0.067  +0.028
   5(1w)     1852       naive_bayes   +0.005  +0.076
  21(1mo)     466        grad_boost   +0.109  +0.144
  63(3mo)     155       naive_bayes   +0.043  +0.139
 126(6mo)      79             ridge   +0.106  +0.322
 252(1y)       40  gaussian_process   +0.228  +0.051   ← 소표본 신기루(신뢰 불가)
 504(2y)       27       (계산 불가, 비겹침 독립창 4개 < 폴드 요구치 6)
```

**해석:**
- 파이프라인 실데이터 완주 ✅. 견고성 수정 후 1y까지 표 생성.
- **1d~6mo**: rankIC 작거나 sd_IC ≥ rankIC → 신호 아님(어제와 동일).
- **1y +0.228 (sd 0.051)**: 표본 40·독립날짜 6개. 어제 "2021 h=10 +0.27"이 소멸한 것과 **동일한
  소표본 과적합 신기루**. 표본이 적어 sd마저 가짜로 작음. **신뢰 불가** — 백필로 확인/반박 필요.
- **2y**: 3년 데이터로는 독립창 부족으로 평가 자체 불가.

→ **파일럿이 본 백필의 필요성을 정확히 증명**한다: 장기 horizon은 데이터 굶주림 상태이고,
유일하게 솔깃한 1y는 표본을 키워야만 판정 가능하다.

## 5. 남은 작업 (재개 절차)

다른 세션의 prod DataLab 운영이 끝나고 **타이밍 조율 후** 진행:

```bash
cd services/agent-worker
# (1) 2016~2020 DataLab을 prod에 백필 (2021~2023은 이미 적재됨). Naver API rate limit 주의.
DATABASE_URL=... python scripts/backfill_datalab.py --start-year 2016 --end-year 2020 \
  --tickers 000100,000270,000660,005380,005930,035420,035720,041510,042700,068270,204320,207940,253450,259960,352820
# (2) 전체 sweep (2016~2026, 7 horizon)
DATABASE_URL=... python scripts/sweep_datalab_horizons.py \
  --tickers <위와 동일> --start 2016-01-01 --end 2026-06-24 --benchmark KS11 \
  --prices-csv prices_kospi15_2016_2026.csv --folds 5 --out-dir sweep_full
# (3) 본 문서 §4에 전체 결과 추가 → 최종 결론 → PR(머지 금지)
```

**판정 기준:** rankIC가 fold 간 일관 + sd_IC 작음 + 독립표본 충분 + baseline 초과일 때만 "신호 있음".
하나라도 불충족이면 "무신호/판정불가(표본부족)".

## 6. 주의/부작용

- 백필은 prod 쓰기(`datalab_raw_documents`/`datalab_raw_details`에 2016~2020 행 추가). 다른 세션과 조율.
- 부작용: `processing_queue`에 NORMALIZE_DATALAB pending 누적(워커 없으면 무해, 사후 정리 가능).
- 새 DB 테이블/마이그 없음(기존 데이터 읽기 + DataLab 행 추가 적재만). 스코프=대체데이터(DATALAB).
- 대용량 CSV(`prices_*.csv`)·sweep 출력은 untracked 유지(커밋 금지).
