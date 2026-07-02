# 특허 단독 ML — 유니버스 34종목 횡단면(레버1) + 장기 저주파(레버4) (2026-06-26)

> 특허 단독 8종목이 무신호였던 데 이어, 표본·변별력을 키우려 **유니버스를 34종목으로 확대**하고 (a) 단기 횡단면 rankIC(레버1)과 (b) 장기 저주파 분기 호라이즌(레버4)을 ML 16모델 워크포워드로 검정했다. 실적재 데이터(prod Supabase, Google Patents BigQuery 2018~2023)·무료 가격(FDR)·count 기반 피처(enrich 없이 $0).
> 
> **한 줄 결론: 무신호 — 레버1 rankIC는 0 중심 산포(최고 +0.016, sd ±0.046)·전 모델 majority baseline 미달; 레버4는 트리(+0.10)/선형(−0.12) 부호 상충 = 과적합 지문. 8종목 → 34종목 횡단면 → 장기 저주파 모두에서 특허 단독 방향성 알파 기각.**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 (Windows 11, CPU) |
| Python / 라이브러리 | Python 3.11.15 · scikit-learn 1.9.0 · pandas 3.0.3 · numpy 2.4.6 |
| DB / 데이터 출처 | Supabase prod (`raw_documents` source_name=GOOGLE_PATENTS) · 가격=FinanceDataReader CSV · BigQuery `patents-public-data` (적재) |

## 2. 실행 메타

- 코드 위치: `services/agent-worker/app/ml/research/` (`bakeoff.py --source patent-db`, `patent_dataset.py`, `patent_db.py`) — **UNTRACKED**(실험 하니스, 신호 전 머지보류).
- 타깃 / 라벨: KOSPI(KS11) 대비 **초과수익률**의 방향(up/down), neutral band로 중립 구간 제외.
- 누수 차단: 모든 특허 피처 윈도잉을 **publication_date(공개일)** 기준 — 특허는 출원 후 ~18개월 비공개라 출원일 사용 시 미래정보 누수. 워크포워드 fold 날짜경계 분할.
- 피처 11종(count 기반, enrich 없음): total · recent_count · prior_count · momentum_ratio · distinct_tech_categories · new_category_count · new_category_ratio · days_since_latest · max/mean_significance · llm_enriched_count.
## 3. 데이터 — 유니버스 · 자료 · 근거

- **유니버스 34종목**(대·중·소 믹스): 영문 assignee LIKE 패턴 수동 큐레이션→BQ 건수·샘플 검증→오염 제거(테스→PILATES, 기아→NOKIA, 리노→타사 등) 후 `scripts/patent_assignee_patterns.json`(영속)으로 확정.
- **적재 결과** (이번 세션, `backfill_patents_bigquery.py --tickers <24신규> --start-year 2018 --end-year 2023`):
| 항목 | 값 |
| --- | --- |
| 자료 | KR 특허 (Google Patents BigQuery) |
| 저장 | prod `raw_documents`(source_name=GOOGLE_PATENTS) |
| 적재 전/후 | 70,262 → **147,288행** |
| inserted / skip / fail | **+77,026 / 4,922 / 0** |
| 종목 수 | 34 전수 (대형주 005930 53,489·066570 LG전자 20,226·005380 현대차 12,161·051910 LG화학 11,983; 희소 종목 다수 50~80행) |
| 가격 | FDR CSV 25,815행 / 35 ticker(34+KS11) / 종목당 739 세션(302440만 689, 후상장) |

- 무결성: 적재 fail 0, 멱등 skip 정상(이미적재분). 누수 차단은 §2 참조.
## 4. 방법론

- 모델: scikit-learn 16종(선형 ridge/lda/logistic, 트리 decision_tree/random_forest/extra_trees/grad_boost/hist_grad_boost, knn, svm_rbf, gaussian_process, naive_bayes, voting/stacking) + baseline_majority/stratified.
- 평가: 워크포워드 fold, fold당 다종목 → **fold rankIC = 사실상 횡단면**(같은 시점 종목 순위 vs 실제 초과수익 순위).
- 지표: accuracy·Dbase(majority 대비 정확도 증분)·IC·rankIC(±fold sd)·decile spread.
- 판정 규칙: rankIC가 (a) 여러 모델 전반 일관 부호 + (b) ±sd 대비 0과 구분 크기 + (c) Dbase>0 → "신호". 하나라도 깨지면 무신호.
## 5. 결과

### 레버1 — 단기 횡단면 (lookback 60 · horizon 5 · band 0.3 · folds 5)

samples=4093 · stocks=34 · dates=147 · up-rate=0.46 · dropped={neutral_band 281, no_forward_price 28, too_few_filings 620}

| 모델 | Dbase | rankIC | sd_IC |
| --- | --- | --- | --- |
| hist_grad_boost | −0.027 | **+0.016** | 0.039 |
| knn / ridge / lda / logistic | −0.026~−0.012 | +0.007 | ~0.035 |
| svm_rbf | −0.001 | +0.004 | 0.049 |
| (중략: gaussian/tree/voting) | ≤0 | −0.000 ~ −0.011 | ~0.05 |
| naive_bayes | −0.055 | −0.017 | 0.046 |
| stacking | −0.000 | −0.051 | 0.021 |

→ rankIC 0 중심 산포(−0.05~+0.016), 최고치도 **sd가 평균의 ~3배**. **전 모델 Dbase ≤ 0**(majority baseline 미달).

### 레버4 — 장기 저주파 (lookback 360 · horizon 60 · band 0.5 · folds 3)

samples=4373 · stocks=34 · dates=136 · up-rate=0.44 · dropped={neutral_band 159, no_forward_price 396, too_few_filings 94}

| 모델군 | 모델 | rankIC | Dbase | dec_sprd |
| --- | --- | --- | --- | --- |
| **트리** | extra_trees | **+0.101** | +0.027 | +5.53 |
|  | decision_tree | +0.082 | +0.008 | +7.21 |
|  | random_forest | +0.071 | +0.016 | +1.83 |
| **선형** | ridge / lda / logistic | **−0.124 ~ −0.126** | −0.021~−0.024 | −10.1~−10.4 |
|  | naive_bayes | −0.082 | −0.030 | −5.52 |

→ 트리군 +0.07~0.10 vs 선형군 −0.12 = **관계 부호 정반대**. 최고 extra_trees도 sd ±0.074 ≈ 평균과 동급. folds=3·horizon 60·lookback 360 → 윈도우 겹침으로 독립 표본 극소.

## 6. 해석 · 판정

- **레버1 = 무신호**: rankIC 0과 구분 불가 + 부호 비일관 + 전 모델 baseline 미달. 표본 4093·34종목으로 변별력 문제 아님 → 특징 자체에 단기 방향성 정보 없음.
- **레버4 = 과적합(견고한 신호 아님)**: 트리(+)/선형(−) 부호 상충은 신호가 아니라 **트리가 일반화 안 되는 비선형 노이즈를 적합**한 전형. 8종목 레버4의 동일 패턴 재현. 소표본(3 fold·겹침)으로 겉보기 rankIC 부풀려짐.
- **가설 기각**: 특허 단독 방향성 알파를, 종목 8→34 횡단면 + 장기 저주파까지 확장해도 **기각**. [[attention-lead-lag-evidence]](대형주 무신호) 및 [[ml-bakeoff-datalab-result]](DataLab 단독 무신호)와 일관.
## 7. 이상치 · 주의 / 한계

- 다중비교: 16모델×2레버 = 32셀에서 우연히 큰 값 1~2개 나오는 건 정상(레버4 트리가 그 사례).
- 희소 종목: 34종목 중 다수가 50~80행(too_few_filings 드롭으로 일부 제외) — 횡단면 폭은 사실상 대형주 위주.
- count 기반만 검정(enrich LLM significance 피처는 무신호 확인 후 $11 절약 위해 미적용) — 단 enrich가 단기 방향성을 살릴 선험적 근거는 약함.
- 결과 CSV(`patent_xs34.csv`·`patent_lf34.csv`)·가격 CSV는 git 비추적(로컬 보관).
## 8. 산출물

- 코드: `app/ml/research/bakeoff.py`(`--source patent-db`)·`patent_dataset.py`·`patent_db.py`(UNTRACKED) · `scripts/backfill_patents_bigquery.py`·`scripts/patent_assignee_patterns.json`(34) · `scripts/backfill_prices_fdr.py`.
- 데이터: prod `raw_documents` GOOGLE_PATENTS 147,288행(34종목). 로컬 `prices34.csv`·`patent_xs34.csv`·`patent_lf34.csv`(비추적).
- 재현(레버1):
```plain text
  uv run python -m app.ml.research.bakeoff --source patent-db --tickers <34> \
    --start 2021-01-01 --end 2023-12-31 --prices-csv prices34.csv --benchmark KS11 \
    --lookback 60 --horizon 5 --band 0.3 --folds 5 --csv patent_xs34.csv
```

레버4: `--lookback 360 --horizon 60 --band 0.5 --folds 3`.

## 9. 다음 단계

- [ ] **특허 단독 트랙 종료** — 방향성 알파 기각 확정. 도구(PR #457)·하니스는 신호 전 머지보류로 보존.
- [ ] **다중소스 융합으로 전환** — 특허·채용·DataLab을 결합한 횡단면(개별 무신호여도 융합 시 잔차 알파 가능성). aggregator 층 설계.
- [ ] (선택) 특허를 방향성 라벨이 아닌 **나우캐스팅/변동성·이벤트** 용도로 재프레이밍 검토(DataLab S9 동시신호 사례와 동형).

---

관련 메모리: [[ml-bakeoff-datalab-result]] · [[attention-lead-lag-evidence]] · [[bigquery-patent-connection]] · 직전(8종목) 리포트 `2026-06-26-patent-ml-xs-lf-leverage.md`
