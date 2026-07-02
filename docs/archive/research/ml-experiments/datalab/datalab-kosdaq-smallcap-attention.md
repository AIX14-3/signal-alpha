# 소형/중형 KOSDAQ 어텐션 횡단면 후속 실험 (2026-06-26)

> 대형주에서 검색 어텐션(네이버 DataLab)이 방향/반전 알파를 못 낸다는 Stage 5~10 결론을 받아,
> 학술상 어텐션 효과의 "서식지"라는 **소형·중형 KOSDAQ**에서 횡단면 반전을 재검정. 종목명 검색(①)
> + 투자의도 키워드(③) + 직전수익 교호작용(④)을 worktree `sa-ml-longhorizon`에서 로컬 CSV로 실행.
> 
> **한 줄 결론: 소형/중형 KOSDAQ에서도 어텐션의 방향/반전 알파는 무신호 — 주간뿐 아니라 일·주·월·분기 전 시간단위에서 횡단면 IC \|t\|>2 도달 0건. "신호처럼 보인" 것(bakeoff rankIC +0.185, raw_level IC t=−4.78)은 모두 DataLab 전체표본 정규화 look-ahead 아티팩트로, 점-시간(PIT)으로 바꾸면 소멸(rankIC 0.185→0.037, raw_level→abn_rollz t −4.78→+0.20). 데이터 무결성 감사(밀도·정합·RAW대조) 결과 정제 파이프라인 결함이 아니라 신호 자체 부재로 확정.**

---

## 1. 실행 환경

| 항목 | 값 |
| --- | --- |
| 실행기 | 로컬 Windows 11 (CPU) |
| Python / 라이브러리 | Python 3.11.15 · scikit-learn + lightgbm/xgboost/catboost · FinanceDataReader · pykrx |
| DB / 데이터 출처 | **DB 미사용**(전부 로컬 CSV) · 네이버 DataLab API(검색) · pykrx(가격) · FDR(유니버스) |

## 2. 실행 메타

- 코드 위치: worktree `sa-ml-longhorizon` / 브랜치 `feat/ml-datalab-longhorizon`(미커밋 working tree). 머지/PR 없음.
- 타깃 / 라벨: **횡단면 초과수익**(주별 종목 H주 수익 − 그 주 종목평균). 방향=부호(중립밴드 |xse|>0.3% 제외).
- 예측 피처(어텐션): `abn`=종목명 검색량의 **과거 26주 롤링 z(과거-only)**, `abn_mom`=abn 1주차분, (누수판) `level`/`mom`=전체표본 z. ④는 `prior_ret`(과거 H주 수익)+`abn×prior_ret`.
- 평가: 5-fold **워크포워드**(시간경계 분할), 16개 분류기 bake-off. 지표 IC·rankIC·accuracy·Dbase(=baseline 대비 정확도)·decile spread·roc_auc.
- 기간/주기: 2021-01-01 ~ 2023-12-31, 주간(ISO week).
## 3. 데이터 — 유니버스 · 자료 · 근거

- **유니버스**: KOSDAQ 시총 백분위 3~45% 밴드에서 섹터 분산 샘플 50종목(관리/투자주의환기/외국기업/스팩 제외). 신규 스크립트 `select_kosdaq_smallcap.py`. → **가격∩검색 보유 46종목**이 실제 분석 모집단.
- 설계 근거: 시총 하위 절반(마이크로캡)은 DataLab 검색량이 사실상 0이라 *리테일 어텐션* 검정 불가 → 검색 가능한 상·중위 KOSDAQ(바이오·2차전지·반도체장비 등 리테일 테마주)이 검정 가능한 서식지.
- 수집 자료:
| 자료 | 출처/API | 저장(로컬) | 행수 | 비고 |
| --- | --- | --- | --- | --- |
| 일봉 종가·거래량 | pykrx(로그인 불필요) | `prices_kosdaq.csv` | 31,585 | 46종목 |
| 종목명 주간 검색 | DataLab API | `stockname_kosdaq.csv` | 7,501 | 49종목 데이터 보유 |
| 의도 키워드 주간 검색 | DataLab API | `intent_kosdaq.csv` | 6,940 | 46종목·74 (종목,키워드)쌍 |

- 의도 키워드 = `"<종목명> 주가/매수/전망"` 3종. 소형주라 `매수`·`전망`은 대부분 검색량 미문→드롭, 살아남은 쌍 평균 1.6개/종목(사실상 `주가` 위주). 다중키워드 합성은 `--composite-search`(주별 z평균).
- **누수 차단**: 피처는 신호주(t0) 이전만(과거 롤링 z·과거 수익), 라벨은 t0 이후 H주만, 워크포워드 날짜 분할. ⚠️ 단 기존 `level`/`mom`은 전체표본 z라 누수 → §6에서 적발·교정.
## 4. 방법론 (판정 규칙)

- **횡단면 반전 IC**: 주별 corr(abn, 미래 횡단면초과수익) 평균. IC<0(반전) & |t|>2 면 신호. 보조: 저관심−고관심 3분위 포트폴리오 스프레드(반전이면 >0), 극단관심(z>2) tail의 미래 초과수익(반전이면 <0).
- **16모델 bake-off**: rankIC가 모델 전반 양(+)·fold 안정(작은 sd) **그리고 majority baseline 돌파(Dbase>0)**면 신호.
## 5. 결과

### 5-1. 종목명 검색(①) — 횡단면 IC (과거-only abn)

| horizon | weeks | IC평균 | IC_t | 저-고 fwd% | tail(z>2) fwd% |
| --- | --- | --- | --- | --- | --- |
| 1주 | 143 | +0.017 | +0.80 | −0.221 | +0.859 |
| 2주 | 142 | +0.019 | +0.92 | −0.161 | +1.024 |
| 1개월 | 140 | +0.004 | +0.20 | +0.153 | +0.882 |

→ 전 horizon IC≈0·|t|<1, tail **양수**(반전 부호 아님). **반전 없음.**

### 5-2. 종목명 검색(①) — 16모델 bake-off (1개월 방향) — **누수 vs PIT 재검증**

| 피처 세트 | 최고 rankIC(모델) | decile spread | roc_auc | Dbase(baseline돌파) |
| --- | --- | --- | --- | --- |
| `[abn, mom, level]` (누수) | **+0.185** (ridge/lda) | +6.30 | 0.576 | +0.000 |
| `PIT[abn, abn_mom]` (교정) | **+0.037** (ridge/lda) | +1.68 | 0.509 | +0.000 |
| `PIT + prior_ret + abn×prior` (④) | +0.035(NB)/+0.003(선형) | ≤+0.7(선형) | ≤0.511 | ≤0 |

→ 누수 제거 시 rankIC 0.185→0.037로 붕괴. 어떤 피처셋도 majority baseline을 못 넘음(Dbase≈0).

### 5-3. 투자의도 키워드(③, composite) — 횡단면 IC + bake-off(PIT+prior)

| horizon | weeks | IC평균 | IC_t | 저-고 fwd% | tail(z>2) fwd% |
| --- | --- | --- | --- | --- | --- |
| 1주 | 143 | +0.004 | +0.18 | +0.064 | +0.619 |
| 2주 | 142 | −0.011 | −0.51 | +0.457 | +0.299 |
| 1개월 | 140 | **−0.035** | **−1.70** | **+1.524** | **−0.461** |

- 1개월에서 IC<0·저-고>0·tail<0 = **반전 부호 3개 정렬** → 다만 **|t|=1.70<2, 유의 미달.**
- bake-off(PIT+prior): 최고 rankIC +0.045(logistic/ridge/lda), decile +2.73, roc_auc 0.516, **Dbase≈0(baseline 미돌파).**
### 5-4. 일간(daily) 검색 — horizon별 횡단면 IC (PIT 롤링z, 비중첩 표본)

종목명 일간 검색(`stockname_daily_kosdaq.csv`, 51,649행/49종목) + 일간 종가. 참조일 간격=horizon으로 표본 비중첩 → t값 정직.

| horizon | 참조표본 n | IC평균 | IC_t | 저-고% | tail% |
| --- | --- | --- | --- | --- | --- |
| 1일 | 708 | +0.012 | +1.40 | −0.091 | +0.227 |
| 3일 | 236 | +0.010 | +0.71 | −0.353 | +0.085 |
| 5일(1주) | 141 | +0.003 | +0.20 | −0.301 | −0.107 |
| 10일(2주) | 70 | +0.017 | +0.64 | −0.935 | +0.770 |
| 20일(1달) | 34 | +0.019 | +0.48 | −0.060 | −1.418 |
| 60일(분기) | 11 | −0.037 | −0.74 | +0.379 | −0.999 |

→ 전 horizon |t|<2. 최댓값 1일 t=+1.40(연속방향·유의 미달).

### 5-5. 검색 집계 시간단위별 영향 (horizon 20일 고정)

| 검색 scale | n | IC평균 | IC_t | 저-고% | tail% |
| --- | --- | --- | --- | --- | --- |
| 일(daily) | 34 | +0.019 | +0.48 | −0.060 | −1.418 |
| 주(5일 평균) | 34 | −0.024 | −0.88 | +1.204 | +0.166 |
| 월(20일 평균) | 34 | −0.013 | −0.41 | +0.207 | −0.528 |

→ 검색을 일·주·월 어느 단위로 집계해도 |t|<1. **시간단위는 결론을 바꾸지 못함.**

### 5-6. 데이터 무결성 감사 (정제 파이프라인이 신호를 죽이나?)

"정제된 피처만 넘겨서 신호를 못 내는 것 아닌가"라는 가설을 직접 검정(`probe_data_integrity.py`).

**(a) 입력 건전성** — 검색 밀도 nonzero주 비율 중앙값 1.00(희박종목 0/49), ratio 0.009~100(중앙값 13, 양자화·클리핑 없음), 검색週↔가격週 정합 어긋남 4/46(단기 IPO). → 입력·정합 정상.

**(b) RAW vs 정제 예측자 (횡단면 IC, 1개월)** ← 핵심

| 예측자 | IC평균 | IC_t | PIT? | 해석 |
| --- | --- | --- | --- | --- |
| `raw_level`(원시 ratio 수준) | −0.073 | **−4.78** | ❌ | 유의하지만 누수 |
| `level_fullz`(전체표본 z) | −0.119 | **−7.49** | ❌ | 유의하지만 누수 |
| `raw_change`(원시 주간변화) | +0.007 | +0.47 | ✅ | 무신호 |
| `abn_rollz`(과거롤링 z, 메인) | +0.004 | +0.20 | ✅ | 무신호 |

DataLab ratio는 **요청 전기간 최댓값=100**으로 정규화 → `raw_level`이 높다=종목의 3년치 검색 정점 근처=미래를 알아야 계산=look-ahead. 그 PIT 버전(`abn_rollz`/`raw_change`)은 무신호. **즉 정제는 신호를 죽인 게 아니라 RAW에 섞인 미래 엿보기 가짜 신호를 올바르게 제거.** §5-2의 0.185→0.037 붕괴와 동일 메커니즘을 RAW 대조로 재확인.

**(c) demean vs 절대수익** — 횡단면 주별 IC는 demean(상수 차감)에 수학적으로 불변 → 이 틀은 *시장 전체 타이밍*을 못 봄(별개 시계열 질문, 대형주 Stage6~9에서 이미 무신호).

## 6. 해석 · 판정

- **확정 결론: 가설 기각.** 소형/중형 KOSDAQ에서도 리테일 어텐션(종목명·의도 검색)은 방향/반전 알파를 만들지 못함. 횡단면 IC가 유의수준(|t|>2)에 도달한 horizon이 **주간·일간·집계 전 단위에서 하나도 없고**, 16모델 bake-off의 rankIC도 baseline을 못 넘음.
- **시간단위 강건성**: 일·주·월·분기 어느 검색 집계/horizon으로 잘라도 무신호(§5-4·5-5). "느린 신호"·"빠른 신호" 가설 모두 기각.
- **정제 결함 아님(무결성 감사, §5-6)**: 입력 밀도·정합 정상. RAW를 직접 써도 PIT-clean에선 무신호이고, 유의해 보인 raw_level(t=−4.78)은 DataLab 전기간 정규화 look-ahead. 정제는 가짜를 제거할 뿐 신호를 죽이지 않음.
- **look-ahead 아티팩트 적발(핵심 수확).** 기존 bake-off의 rankIC +0.185는 `level`/`mom` 피처를 **전체 3년 표본**으로 z-score(미래 평균/분산 누출)한 데서 나온 가짜였음. 피처를 과거-only(PIT)로 바꾸자 +0.037로 붕괴 → 신호 아님 확정. 향후 모든 횡단면 실험에 `--pit-features` 점검을 표준화.
- **유일한 속삭임**: 의도 키워드 1개월 반전(IC −0.035, t −1.70, 부호 3정렬). 유의 미달·ML baseline 미돌파라 **거래 불가**. 더 큰 소형 유니버스에서 재현되는지가 후속 확인거리(단독으로 추격할 가치는 낮음).
## 7. 이상치 · 주의 / 한계

- **생존편향**: FDR는 현재 상장 종목만 → 2021~23 중 상폐된 소형주 누락(표본이 위로 편향). 반전(하락주) 검정엔 불리한 누락.
- **다중비교**: 3 horizon × 3 피처셋 × 2 검색종류 → t≈1.7 한 건은 다중비교상 우연 범위.
- **소형주 검색 희박**: 의도 키워드 합성은 사실상 `주가` 단일에 수렴(매수/전망 드롭). "의도" 구성개념을 충분히 분리 못 했을 수 있음.
- 결과 CSV/로그·유니버스 JSON은 **git 비추적**(데이터 산출물 커밋 금지 규칙).
- `tests/ml/test_contract_adapter.py`는 `vol_models` 미설치로 사전부터 collection-error(이번 변경과 무관).
## 8. 산출물

- 신규 코드: `scripts/select_kosdaq_smallcap.py`(유니버스), `scripts/cross_sectional_attention.py`에 `--pit-features`/`--prior-return`/`--composite-search` 플래그, `scripts/cross_sectional_daily.py`(일간 횡단면 2축), `scripts/probe_data_integrity.py`(무결성 감사).
- 로컬 산출(비추적): `kosdaq_smallcap.json`·`prices_kosdaq.csv`·`stockname_kosdaq.csv`·`stockname_daily_kosdaq.csv`·`intent_kosdaq.csv`·`kw_kosdaq/`·`kw_intent/`·`xs_*.log`.
- 재현(한 줄, worktree `sa-ml-longhorizon/services/agent-worker`):
```bash
  uv run --with finance-datareader python scripts/select_kosdaq_smallcap.py --n 50 --out kosdaq_smallcap.json --kw-dir kw_kosdaq
  uv run --with pykrx python scripts/collect_ohlcv_pykrx.py --tickers <50> --start 2021-01-01 --end 2023-12-31 --out prices_kosdaq.csv
  uv run python scripts/collect_datalab_for_keywords.py --kw-dir kw_kosdaq --out stockname_kosdaq.csv --start 2021-01-01 --end 2023-12-31 --time-unit week
  PYTHONIOENCODING=utf-8 uv run python scripts/cross_sectional_attention.py --prices-csv prices_kosdaq.csv --search-csvs stockname_kosdaq.csv --tickers <50> --pit-features
```

## 9. 다음 단계

- [ ] (선택) 의도 키워드 1개월 반전을 더 큰 소형 KOSDAQ(100+) + 생존편향 보정(상폐 포함) 패널에서 재현 확인 — 재현 안 되면 어텐션 단독 트랙 종결.
- [ ] **레버 전환: 다중소스 융합**(검색×채용×특허) — 단일 대체데이터의 방향 알파는 대형(Stage5~10)·소형(본 실험) 모두 기각. 검색→*펀더멘털*(②, 분기 실적 선행)은 별도 규모로 분리.
- [ ] Part 1(longhorizon PR화)는 별 세션 — origin/main 위 rebase(현재 236커밋 stale) 필요.

---

관련 메모리: [[ml-bakeoff-datalab-result]] · [[attention-lead-lag-evidence]] · [[experiment-report-routine]]
