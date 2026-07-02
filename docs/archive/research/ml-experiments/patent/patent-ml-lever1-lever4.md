# 특허 ML 신호 — 레버1(종목확대 횡단면) · 레버4(장기 저주파) (2026-06-26)

> 3종목·월별 선행성 무신호([[attention-lead-lag-evidence]]) 후속. 특허를 ML 바이크오프 하니스에 새 소스(`patent-db`)로 연결하고, 종목을 3→8로 늘려 **횡단면 rankIC**(레버1)와 **장기 저주파 피처**(레버4)를 검증. 룩어헤드 안전(공개일 기준). 실데이터(Supabase prod 특허 + FinanceDataReader 주가).
> 
> **한 줄 결론: 두 레버 모두 robust 신호 없음 — 레버1 선형 rankIC ~+0.03(노이즈 범위), 레버4 트리 +0.07~0.10 vs 선형 −0.15로 상충·불안정(과적합); 근본 병목은 assignee 자동매핑이 신뢰 종목을 8개로 캡한 것.**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 Windows 11 (GPU 불필요) |
| Python / 라이브러리 | 3.11 (uv) · scikit-learn · scipy · asyncpg · FinanceDataReader 0.9.202 · google-cloud-bigquery |
| 주가 출처 | FinanceDataReader → `ticker,date,close` CSV (ohlcv_data 미접촉) |
| 특허 출처 | Supabase prod (서울 ap-northeast-2) — `patent_raw_details`(`source_name='GOOGLE_PATENTS'`, enriched) |
| LLM | Gemini 2.5-flash-lite (assignee 영문명 생성 + significance enrich) |

## 2. 실행 메타

- 코드(미커밋, app/ml은 동시세션 영역): `app/ml/research/patent_dataset.py`·`patent_db.py`(datalab 모듈 미러) + `bakeoff.py` `--source patent-db`. 도구: `select_universe_fdr.py`·`build_patent_patterns.py`·`seed_universe_stocks.py`. 특허 적재/enrich = PR #457 계열.
- 라벨: KOSPI(KS11) 대비 forward 초과수익률, neutral band, y_direction(up/down) + excess_return(회귀).
- 룩어헤드: 피처 윈도잉/모멘텀을 **publication_date(공개일)** 기준(특허 ~18개월 비공개 → 출원일 사용 금지). `compute_indicators`에 공개일을 넣어 모멘텀도 공개시점 기준.
- 평가: 워크포워드(날짜경계), 16+ 모델, 지표 IC·rankIC(per-fold 다종목=횡단면)·Dbase(=acc−majority)·decile_spread.
- 스윕: 레버1 lookback 60/horizon 5/band 0.3/5fold; 레버4 lookback 360/horizon 60/band 0.5/3fold.
## 3. 데이터 — 유니버스 · 자료 · 근거

- **유니버스 구축 파이프라인**: FDR `KRX-DESC`(Industry/Products)로 R&D 집약 1,719 후보 → 53 추림 → Gemini 영문 assignee명 → BigQuery 매칭 프로브(2016~2023) → **사람 검수** → **깨끗한 8종목**. (병목·오염은 §7.)
- 8종목: 삼성전자·SK하이닉스·NAVER(기존 enrich) + 기아·한미약품·레이언스·코맥스·하나기술(신규).
- 적재(신규): 기아 7,451·한미 319·레이언스 75·코맥스 55·하나기술 76 = **7,976건, 실패 0**. enrich 성공 7,963·실패 13·**$0.79**.
- 주가: 8종목+KS11 일봉 739세션(2021~2023), FDR.
- 무결성/누수: 공개일 기준 윈도(≤as_of), 라벨은 이후 가격, 워크포워드 날짜경계. 소형주 공개특허 희박분은 `too_few_filings`로 명시 탈락.
## 4. 방법론

- 피처(`compute_indicators`): total·recent/prior·momentum_ratio·new_category(_ratio)·distinct_tech·days_since_latest·llm_enriched_count·mean/max significance (11개).
- 판정: k>0 fold rankIC가 **양(+)으로 모델 전반 일관 + 안정(작은 sd)** 이면 신호. n에 비해 |rankIC|가 노이즈(~1/√n) 초과해야.
## 5. 결과

### 레버1 (횡단면, lookback 60·horizon 5, samples=795, 8종목·147일)

| 모델 | Dbase | IC | rankIC | dec_spread |
| --- | --- | --- | --- | --- |
| naive_bayes | −0.04 | +0.057 | +0.033 | +0.99 |
| lda | +0.01 | +0.067 | +0.032 | +1.55 |
| logistic | +0.01 | +0.063 | +0.027 | +1.32 |
| ridge | +0.01 | +0.072 | +0.027 | +1.32 |
| 트리계열 | ≤0 | ~0 | ~0/− | — |

### 레버4 (저주파, lookback 360·horizon 60, samples=964)

| 모델군 | rankIC | Dbase | 비고 |
| --- | --- | --- | --- |
| 트리/앙상블(stacking·extra_trees·dtree·rf) | +0.07~+0.10 | +0.02~+0.07 | sd_IC 0.11~0.19(불안정), stacking dec_spread +14% |
| 선형/NB(logistic·nb·ridge·lda·svm) | −0.11~−0.18 | −0.04~−0.08 | 강한 음수 |

## 6. 해석 · 판정

- **레버1**: 선형모델이 일관된 작은 +(rankIC ~0.03)를 보여 3종목(전부 0/−)보다 방향성 생김. 그러나 n=637에서 0.03은 ~1σ = **통계적 무신호**.
- **레버4**: 트리(+) vs 선형(강한 −) **상충** + 변동성 극심 → 단조 신호 부재, **소표본 과적합 아티팩트**(stacking +14% decile_spread는 신기루). **무신호**.
- **가설 기각**: 8종목 규모로는 특허가 횡단면/저주파 초과수익을 robust하게 예측하지 못함. [[attention-lead-lag-evidence]]·[[ml-bakeoff-datalab-result]](단일 대체데이터 무신호)와 일관.
## 7. 이상치 · 주의 / 한계 (핵심 = 병목)

- **assignee 자동매핑 병목**: 53종목 중 자동 신뢰 8개뿐. 검수에서 차단한 오염:
- 제네릭 약어 동명이인: 나노 `%NANO%`(2457)·아이에이 `%IA%`·브이엠 `%VM%`·썬테크(중국 Suntech).
- **기아 `%KIA CORP%` → NOKIA CORP** substring 매칭(12k 오염) → `%KIA MOTORS%`만.
- 한미 `%HANMI PHARM%` 베이징한미(중국 자회사) 경미 혼입.
- 가짜 탈락(Gemini 영문명 오류): 파두→FARADAY(실제 FADU)·NCSOFT·서진시스템·가온칩스 등 → 0~3건으로 부당 탈락.
- 소표본: 8종목·~3년·소형주 희박(`too_few_filings` 327/94). 대형주 편중.
- 결과 CSV/JSON 비추적.
## 8. 산출물

- 하니스: `app/ml/research/patent_dataset.py`·`patent_db.py`, `bakeoff.py --source patent-db`.
- 도구: `scripts/select_universe_fdr.py`·`build_patent_patterns.py`·`seed_universe_stocks.py`·`patent_assignee_patterns.json`.
- 결과(로컬 비추적): `patent_xs.csv`(레버1)·`patent_lf.csv`(레버4).
- 재현: `python -m app.ml.research.bakeoff --source patent-db --tickers <8> --prices-csv <fdr> --benchmark KS11 --lookback 60 --horizon 5 --band 0.3 --folds 5`.
## 9. 다음 단계

- [ ] **병목 해소 = assignee 수동 교정으로 30~50종목 확대** (진행 예정): Gemini 오류명 교정(FADU·NCSOFT·서진시스템…) + 섹터별 대표 R&D사 정확 패턴 직접 작성 + BQ 프로브 검증 → 횡단면 통계력 확보 후 레버1/4 재실행.
- [ ] 타깃 전환(변동성/거래량 매그니튜드), 다중소스 융합(특허+채용+DataLab)은 후순위.

---

관련 메모리: [[attention-lead-lag-evidence]] · [[ml-bakeoff-datalab-result]] · [[bigquery-patent-connection]]
