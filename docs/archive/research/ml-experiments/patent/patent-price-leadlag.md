# 특허 데이터 → 주가 선행성(lead-lag) 작은 검증 (2026-06-25)

> 적재·enrich한 특허(3종목, 2016~2023)가 주가를 **선행**하는지 작게 검증했다. 특허를 공개일(publication_date) 기준 월별 피처로 만들고(룩어헤드 방지), KOSPI 대비 월간 초과수익률과의 시차별 순위상관(Spearman IC)을 계산. 실데이터(Supabase prod 특허 + FinanceDataReader 주가).
> 
> **한 줄 결론: 선행 신호 없음 — 미래시차(k=+1,+2) IC가 0~음수, 평균 significance는 전 구간 ≈0, 최대 |IC|=0.30도 후행(k=−2)이며 n~100에서 노이즈와 구분 불가. 대형주는 특허가 수익률 방향을 선행하지 않는다([[attention-lead-lag-evidence]]와 일치).**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 Windows 11 (GPU 불필요) |
| Python / 라이브러리 | 3.11 (uv) · asyncpg · FinanceDataReader 0.9.202 |
| 주가 출처 | FinanceDataReader → `ticker,date,close` CSV (ohlcv_data 미접촉, CSV 경로만) |
| 특허 출처 | Supabase prod (서울 리전 ap-northeast-2) — `patent_raw_details` (`source_name='GOOGLE_PATENTS'`, enriched) |

## 2. 실행 메타

- 코드: 일회성 프로브 `scratchpad/patent_leadlag_test.py`(비추적), 가격 재생성 `scripts/backfill_prices_fdr.py`(PR #457 무관, 기존).
- 타깃(라벨): **KOSPI(KS11) 대비 월간 초과수익률** = 종목 월수익률 − 지수 월수익률.
- 피처 타임스탬프: **publication_date(공개일)** — 출원일은 ~18개월 비공개라 룩어헤드. 공개일이 "시장이 알 수 있던 시점".
- 시차 스윕: k ∈ {−2,−1,0,+1,+2} (k>0 = 특허가 주가 선행).
- 풀링: 종목 내 z-score 후 3종목 풀, Spearman 순위상관(동점 평균순위).
## 3. 데이터 — 유니버스 · 자료 · 근거

- 유니버스: 삼성전자(005930)·SK하이닉스(000660)·NAVER(035420). **3종목·전부 대형 다출원사** → 횡단면 불가, 대형주 prior상 방향 알파 약함.
- 자료:
| 자료 | 출처 | 저장 | 규모 | 기간 |
| --- | --- | --- | --- | --- |
| 특허 월별 집계(공개건수·평균/합 significance) | prod patent_raw_details | 쿼리 집계 | 종목당 35 patent-months | 공개일 2021~2023 |
| 일봉 종가 + KOSPI | FinanceDataReader | 로컬 CSV(비추적) | 739 세션/티커 ×4 | 2021~2023 |

- 무결성: 종목별 35 patent-months = 35 return-months(완전 정합). 특허 significance는 `llm_status='success'` 행만 평균/합.
- 누수 차단: 피처=공개일 기준(미래 미사용), 라벨=해당월 이후 수익률, 시차 정렬로 t→t+k 분리.
## 4. 방법론

- 피처 3종: `count`(월 공개 건수), `mean_sig`(월 평균 LLM significance), `sig_sum`(중요도 합 = 양×질).
- 지표: Spearman rank IC(피처 t vs 초과수익률 t+k). 판정: k>0에서 **양(+) IC가 유의**하면 "선행 신호 있음".
- 유의성 기준: n≈100에서 |IC|≳0.2가 대략 2σ. 그 미만은 0과 구분 불가로 본다.
## 5. 결과

Spearman IC — feature(t) vs excess_return(t+k):

| 피처 | k=−2 | k=−1 | k=0 | k=+1 | k=+2 | n |
| --- | --- | --- | --- | --- | --- | --- |
| count | −0.295 | −0.075 | −0.157 | −0.161 | −0.010 | 96~102 |
| mean_sig | −0.051 | +0.095 | −0.071 | +0.016 | +0.019 | 96~102 |
| sig_sum | −0.299 | −0.065 | −0.171 | −0.167 | −0.013 | 96~102 |

- 미래시차(k=+1,+2): count/sig_sum 음수(−0.16~−0.01), mean_sig ≈0 → **양의 선행 없음**.
- 최대 절대값 k=−2(−0.30)는 **후행**(주가가 2개월 먼저). 약하고 다중비교(15칸) 노이즈 가능.
## 6. 해석 · 판정

- **가설 기각**: 특허(건수·중요도)가 대형주 월간 초과수익률을 양(+)으로 **선행하지 않는다.** mean_sig 무력 → LLM 중요도도 수익률 예측엔 무신호.
- 약한 음의 동시/후행 상관은 표본·다중비교 노이즈로 해석(액션 불가).
- [[attention-lead-lag-evidence]](대형주 방향 LEAD 거의 없음)·[[ml-bakeoff-datalab-result]](DataLab 단독 무신호)와 일관 — **단일 대체데이터·소수 대형주로는 방향 알파 안 나옴** 패턴 재확인.
## 7. 이상치 · 주의 / 한계

- 3종목·35개월·월별 → 표본 극소, 통계력 약함.
- 대형주 한정(중소형 미포함) → 효과가 있을 영역을 안 봄.
- 다중비교 15칸 → 우연 비영(非零) 주의.
- BQ 18개월 공개지연으로 최신분 부재. 결과 파일 비추적.
## 8. 산출물

- 프로브: `scratchpad/patent_leadlag_test.py`(공개일 월별 집계 + 시차 IC, 순수 파이썬).
- 가격: `scripts/backfill_prices_fdr.py`로 `prices_2021_2023.csv` 재생성(비추적).
- 재현: `uv run python patent_leadlag_test.py prices_2021_2023.csv` (DATABASE_URL 필요).
- 특허 데이터·enrich: PR #457 / [[bigquery-patent-connection]].
## 9. 다음 단계 (각각 별도 실험 예정)

- [ ] **레버 1 — 종목 유니버스 확대**: 3 → 수십~수백(특히 중소형주). 횡단면 IC/rankIC로 재검증. BigQuery 적재 + enrich 확장 필요.
- [ ] **레버 4 — 장기 누적 저주파 피처**: 월별 스냅 대신 12개월 누적 R&D 모멘텀·기술전환·신규카테고리 진입을 피처화해 분기/반기 호라이즌 검증.
- (보조) 타깃 전환(변동성/거래량 매그니튜드), 다중소스 융합은 후순위.

---

관련 메모리: [[attention-lead-lag-evidence]] · [[ml-bakeoff-datalab-result]] · [[bigquery-patent-connection]]
