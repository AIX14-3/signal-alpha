# 시계열 예측 모델 선정 조사 보고서

> **대상 프로젝트**: signal-alpha (FastAPI 단일 백엔드, 멀티에이전트 투자 신호 분석)
> **작성일**: 2026-06-18
> **스코프**: 일봉(daily OHLCV) · 수일~수주 horizon · 세 가지 예측 타깃(① 가격/수익률 방향 ② 변동성/거래량 ③ 외생피처 조건부)
> **결과물 성격**: 후속 도입 의사결정을 위한 근거 문서 (구현·코드 통합 없음)

---

## 1. 요약 (Executive Summary)

- **핵심 결론**: 시계열 파운데이션 모델(TSFM)을 **가격 오라클로 쓰지 말 것.** signal-alpha 멀티에이전트 신호 체계에 **하나의 확률적 피처**로 합류시키는 것이 올바른 도입 형태다.
- 사용자가 고른 1차 후보(TimesFM 2.5 / Kronos-base / Chronos-2)는 **벤치마크 후보군으로서 합리적**이나, 셋 중 금융 데이터로 실제 학습된 것은 **Kronos 하나뿐**이며, 이 프로젝트에서 가장 결정적인 축인 **공변량(covariate) 지원**이 선정 기준에서 빠져 있었다.
- **타깃별 1순위 후보 요약**

  | 타깃 | 1순위 | 대안 | 필수 베이스라인 |
  |---|---|---|---|
  | ① 가격/수익률 방향 | Kronos-base | FinCast | naive last-value, LightGBM |
  | ② 변동성/거래량 | Kronos-base | Chronos-2 | GARCH |
  | ③ 외생피처 조건부 | Chronos-2 | Moirai-2 / Moirai-MoE | LightGBM(+외생피처) |

- **기대치 설정**: ①은 random walk에 가까워 ROI가 낮고 가장 어렵다. ②·③이 투자 대비 효용이 높다. 도입 우선순위를 ② → ③ → ① 순으로 둘 것을 권고한다.

> **갱신(2026-06-18)**: 실제 입력 피처셋(네이버 키워드·채용공고·주가정보·DART 공시분석·증권사 리포트 지표)이 확인되어 **§1.5 피처셋 기반 재분석**을 추가했다. 이 피처셋은 모델 선정의 1차 기준을 "공변량 수용력"으로 못박으며, **예측 본체는 파운데이션 모델이 아니라 트리/TFT**가 되고 Kronos·Chronos-2는 피처 생성기로 재배치된다.

---

## 1.5 피처셋 기반 재분석 (실제 입력 반영)

signal-alpha ML이 실제 사용하는 입력은 다음 5종이다:
**① 네이버 키워드 ② 채용공고 ③ 주가정보 ④ DART 공시문서 분석 ⑤ 증권사 리포트 분석 지표.**

이는 대부분 **이벤트성·혼합주파수 외생 신호(alternative data)**이며, 문제 클래스를 "시계열 예측"에서 **"대체데이터 기반 공변량 예측/nowcasting"**으로 바꾼다. 따라서 모델 선정의 1차 기준은 **"공변량을 얼마나 잘 수용하는가"**다.

### 피처 성격

| 피처 | 빈도 | 성격 | 입력 형태 |
|---|---|---|---|
| 네이버 키워드 | 일별 | 연속·관심도 급증(선행) | 과거 관측 공변량(z-score, 이상치) |
| 채용공고 | 주/월·불규칙 | 펀더멘털 nowcast(저속) | 느린 공변량(forward-fill) |
| 주가정보 | 일/장중 | 타깃이자 피처(RV·모멘텀) | 타깃 + 과거 공변량 |
| DART 공시분석 | 이벤트성 | 희소·발생시점 중요 | 이벤트 피처(decay, 더미) |
| 증권사 리포트 지표 | 이벤트성 | 목표가·등급 개정(강한 선행) | 이벤트 피처(개정 폭) |

### ⚠️ 핵심 정정 — Kronos는 5개 중 "주가"만 먹는다
Kronos 입력은 **OHLCV 뿐**이라 네이버·채용·DART·리포트 **4개를 전부 버린다.** 따라서 "5개 피처로 예측" 설계에서 **Kronos는 예측 본체가 될 수 없고**, 주가/변동성 패턴만 보는 부품이다. Chronos-2/Moirai-2는 공변량을 받지만 *매끄러운 시계열* 공변량 가정이라 희소·이벤트 피처(DART·리포트)는 엔지니어링이 필요하다.
→ **5개 피처를 통합하는 본체는 트리/TFT여야 한다.**

### 재정렬된 2단 아키텍처 (Kronos+Chronos-2 직관 보존)
```
[피처 생성층]                          [통합·예측 본체]
 Kronos    → 주가/변동성 패턴       ┐
 Chronos-2 → 공변량 포함 변동성     ┤→  LightGBM/CatBoost  → 변동성·거래량
 RV(장중캡처)·HAR 항                 ┤    또는 TFT            (5개 피처 전부 통합)
 네이버/채용/DART/리포트 엔지니어피처┘
```
- **통합 본체 1순위: LightGBM/CatBoost** — 이종 tabular·결측에 강하고 SHAP로 소스별 기여 설명 가능(리포트·공시 기반이라 설명력 중요).
- **대안: TFT** — 정적+미래known+과거관측 공변량 네이티브, 다중horizon·확률·변수중요도. 단 데이터 적으면 "random guess 수준" 보고 있음 → 충분할 때만 LightGBM과 경쟁.
- **Kronos/Chronos-2** = 주가 채널 피처 생성기(stacking). 이것이 "신호의 한 피처" 도입 형태의 구체화.

### 혼합주파수 변동성 → GARCH-MIDAS
채용공고(저속)·키워드(일별)·공시(이벤트)가 섞인 혼합주파수 변동성의 정석 베이스라인은 **GARCH-MIDAS**(저주파=장기성분, 일별=단기성분). HAR-RV(장중캡처 기반 RV)와 함께 **반드시 이겨야 할 기준선**.

### 피처 엔지니어링 (point-in-time 필수)
- **네이버 키워드**: 비정상 검색 급증(z-score, 7d/30d 편차) → 변동성·거래량 선행.
- **채용공고**: 건수 변화율·전년동월비, forward-fill.
- **DART 공시**: 경과일·이벤트유형 더미·추출지표 + 시간감쇠. **접수시각(rcept) 이후에만 사용.**
- **증권사 리포트**: 목표가 개정폭·등급 변경·EPS 상향 breadth·빈도. **발행시각 이후에만 사용.**

### 🚨 최대 리스크 — look-ahead
DART·리포트는 **이벤트 데이터**라 발행 *이전*에 반영하면 **가짜 알파**가 생긴다. 모든 이벤트 피처는 공시/발행 타임스탬프 이후 시점에만 사용하도록 **point-in-time 정합을 강제**할 것. 워크포워드 백테스트 신뢰성을 좌우하는 #1 요인.

---

## 2. 현실 전제 (가장 중요)

> 이 섹션을 건너뛰면 나머지 평가의 의미가 왜곡된다.

1. **일봉 수익률 방향 예측은 random walk에 근접하고 신호 대 잡음비(SNR)가 극도로 낮다.** 효율적 시장에 가까울수록 다음 봉의 방향은 거의 동전 던지기다.
2. **GIFT-Eval·범용 벤치마크 SOTA ≠ 금융 수익률 예측 성능.** 에너지·교통·날씨·리테일에서 강한 모델이 금융 수익률에서 **단순 "마지막 값 유지(naive last-value)" 베이스라인을 못 이기는 경우가 흔하다.**
3. **변동성·거래량·유동성은 가격 방향보다 예측 가능성이 높다.** 변동성 클러스터링(GARCH 효과)이라는 구조가 존재하기 때문이며, 리스크 신호로서 ROI도 더 좋다.
4. **모든 비교의 하한선 베이스라인을 반드시 둘 것**:
   - `naive last-value` (방향/점예측의 절대 기준)
   - `ARIMA` (점예측)
   - `GARCH` (변동성)
   - `LightGBM` (엔지니어링 피처 기반, 종종 TSFM을 이김)

   **이 베이스라인들을 통계적으로 유의하게 이기지 못하면, 해당 타깃에 대한 파운데이션 모델 도입은 보류가 정답이다.**

---

## 3. 선정 축 재정의

사용자의 1차 선정 기준은 **"파인튜닝 가능 + 금융 학습"** 이었다. 타당하지만 다음을 보완해야 한다.

- **금융 학습 여부**: 후보 3종 중 실제 금융 데이터 학습 모델은 Kronos뿐. TimesFM·Chronos-2는 범용 모델이다(이 분류를 정확히 인지할 것).
- **추가해야 할 결정 축 — 공변량/다변량 지원**: signal-alpha의 차별점은 **풍부한 외생 변수**다.
  - DART L2 지분/내부자 이벤트(`dart_ownership_events`)
  - DART L3 임직원 통계(`dart_employee_stats`)
  - 장중 호가·체결·프로그램매매(intraday capture)

  단변량(univariate) 모델은 이 자산을 **전혀 활용할 수 없다.** 따라서 "외생피처를 조건으로 받을 수 있는가"가 이 프로젝트에서 모델 가치를 가르는 핵심 축이다.

---

## 4. 1차 후보 평가 (사양 검증 완료)

| 모델 | 파라미터 | 금융 특화 | 공변량/다변량 | 컨텍스트 | 최대 horizon | 라이선스 |
|---|---|---|---|---|---|---|
| **Kronos-base** | 102M (mini 4.1M ~ large 499M) | ✅ 12B K-line / 45개 글로벌 거래소, OHLCV+amount | ❌ (OHLCV 내부 채널만) | 512 (mini 2048) | 생성형 샘플링 | MIT |
| **Chronos-2** | 120M (encoder, T5계열) | ❌ 범용 | ✅ 단변량/다변량/과거+미래 known 공변량 | 8192 | 1024 | Apache-2.0 |
| **TimesFM 2.5** | 200M (decoder) | ❌ 범용 | △ 단변량 위주 | 1024+ | 가변 | Apache-2.0 |

### Kronos-base
- **유일한 금융 네이티브 모델.** 12B K-line 레코드/45거래소 학습, 입력은 `[open, high, low, close]`(+ optional `volume, amount`) pandas DataFrame.
- 논문(arXiv 2508.02739) 주장: 가격 시계열 RankIC **+93%(vs 최고 TSFM)**, 변동성 MAE **−9%**, 합성 K-line 생성 충실도 +22%, **25개 베이스라인** 비교. 단 random walk와의 직접 비교는 논문에 명시되지 않음 → **자체 검증 필요.**
- **구조적 제약**: 토크나이저 + 디코더-온리 **생성형(경로 샘플링)** 모델이라 출력이 OHLCV로 닫혀 있다. 외부 신호(공시·호가)를 조건으로 주입하기 어렵다.
- 일봉 512 컨텍스트 ≈ **약 2년치**로 수일~수주 예측에 충분.

### Chronos-2
- **외생 피처 결합이 핵심이면 1순위.** 과거 공변량 + **미래 known 공변량**(예: 발표 예정 공시 더미, 매크로 일정, 만기일)을 동시에 받는다.
- zero-shot SOTA, A10G GPU 단일로 초당 300+ 시계열, CPU 추론 지원.
- 일봉엔 8192 컨텍스트가 과잉이나 무해.
- 금융 학습은 아니므로 ②·③에서 **파인튜닝 또는 외생피처 결합으로 가치를 끌어내야** 한다.

### TimesFM 2.5
- 단단한 **범용 단변량 베이스라인.** zero-shot 비교의 기준점으로 좋다.
- 그러나 이 프로젝트의 차별점(외생 피처)을 살리지 못해 **주력 후보로는 부적합.**

---

## 5. 추가 추천 모델

| 모델 | 분류 | 강점 | signal-alpha 적합성 |
|---|---|---|---|
| **FinCast** (arXiv 2508.19609) | 금융 전용 TSFM | Kronos와 같은 금융특화 카테고리 | **Kronos 직접 비교군으로 반드시 포함** |
| **Moirai-2 / Moirai-MoE** (Salesforce) | 범용, any-variate | 공변량·교차자산, 소량 데이터 few-shot 파인튜닝 강함 | ③ 외생피처 조건부에서 Chronos-2 대안 |
| **IBM Granite TTM / TinyTimeMixer** | 초경량(1~5M) | 외생 채널 지원, CPU 파인튜닝, 저비용 | **FastAPI 단일 백엔드 배포 비용 측면 매력적** |
| **Toto** (Datadog) | 범용, 2026 오픈웨이트 SOTA | 고빈도·관측성 데이터 강점 | 일봉보단 장중(intraday)용 참고 |
| **Time-MoE** | billion-scale 희소 MoE | 파인튜닝 비용 효율(출력블록+MoE 일부만 업데이트) | 대형 모델 옵션 |
| **PatchTST / iTransformer** | 지도학습(파운데이션 X) | 자체 데이터 충분 시 TSFM 상회 빈번 | 강력한 비교군 베이스라인 |
| **LightGBM / CatBoost** | 트리(tabular) | 이종 대체데이터·결측 강함, SHAP 설명 | **5개 피처 통합 예측 본체 1순위(§1.5)** |
| **TFT** | 딥러닝(공변량 네이티브) | 정적+미래known+과거 공변량, 다중horizon·확률·변수중요도 | 통합 본체 대안(데이터 충분 시) |
| **DeepAR** | 확률 RNN | 양수·곱셈적 분포 예측 | **거래량 전용** 권장 |
| **HAR-RV / GARCH-MIDAS** | 계량(변동성) | 실현변동성·혼합주파수 표준 | ②의 **필수 베이스라인** |

> 시사점: "파운데이션 모델 vs 자체 지도학습 모델"은 열려 있는 경쟁이다. 더구나 실제 입력이 이종 대체데이터(§1.5)라 **트리(LightGBM/CatBoost)·TFT가 통합 예측 본체로 우승할 가능성**이 높다. 파운데이션 모델은 주가 채널 피처 생성기로 둘 것.

---

## 6. 타깃별 권고 (일봉, 수일~수주)

### ① 가격 / 수익률 방향
- **기대치 보수적으로.** random walk 근접 영역.
- 후보: **Kronos / FinCast**(금융특화). 비교 베이스라인 **naive last-value, LightGBM 필수.**
- 메트릭: 방향 정확도, **RankIC**(횡단면 순위 상관), 거래비용 반영 후 IR.
- 판단 기준: naive 대비 유의한 우위가 없으면 **신호 피처로만 제한 사용 또는 보류.**

### ② 변동성 / 거래량 (ROI 최고 영역) — **도입 1순위**
- 구조(변동성 클러스터링)가 존재해 가장 현실적인 도입 대상. 사용자도 1순위로 확정.
- **2단 아키텍처(§1.5)**: **예측 본체 = LightGBM/CatBoost(1순위) 또는 TFT**가 5개 피처 전부 통합. **Kronos·Chronos-2는 주가 채널 피처 생성기**로 stacking.
- 필수 베이스라인: **HAR-RV**(장중캡처 RV), **GARCH-MIDAS**(혼합주파수), EGARCH/EWMA. 이걸 못 이기면 보류.
- 보조 분포 모델: **Toto**(고빈도·분포), **Moirai-2**.
- **거래량은 변동성과 분리**: 양수·곱셈적·요일효과 → 로그변환 + 분포예측(DeepAR/Moirai-2).
- 메트릭: 변동성=**QLIKE**, 거래량=**CRPS/MASE**, 실현변동성 대비.

### ③ 외생피처 조건부 예측
- signal-alpha의 차별점을 직접 활용하는 영역.
- 후보: **Chronos-2 / Moirai-2**(미래 known 공변량 1순위).
- **피처 주입 설계 스케치**:
  - 미래 known 공변량: 실적 발표 예정일 더미, 배당락일, 옵션 만기일, 매크로 캘린더.
  - 과거 공변량: 일별 집계된 DART 지분 이벤트 카운트, 내부자 거래 플래그, 프로그램매매 순매수, 호가 불균형(OBI) 일별 요약.
  - 타깃: 익일~수주 누적 수익률 또는 실현변동성.
- 베이스라인: 동일 외생피처를 넣은 **LightGBM**(공변량 활용 능력의 정직한 비교 기준).

---

## 7. 평가 프로토콜 권고 (제안만 — 본 작업 범위 외)

- **워크포워드(rolling-origin) 백테스트.** 미래 정보 누수(look-ahead) 철저 차단 — 특히 DART 공시는 **공시 시각(rcept) 기준**으로만 피처화.
- 메트릭 매핑:
  - 방향: 정확도, RankIC
  - 점예측: MASE, MAE
  - 확률예측: CRPS, pinball loss
  - 변동성: QLIKE
- **실무 메트릭**: Annualized Excess Return(AER), Information Ratio(IR) — **거래비용·슬리피지 반영 필수.**
- 모든 모델은 절대 점수가 아니라 **naive·ARIMA·GARCH·LightGBM 대비 상대 성능**으로 보고.
- 통계적 유의성: Diebold-Mariano 검정 등으로 베이스라인 우위 확인.

---

## 8. 최종 권고 매트릭스 & 도입 로드맵 제언

### 권고 매트릭스 (피처셋 §1.5 반영 — 2단 구조)

| 타깃 | 예측 본체(통합) | 피처 생성기 | 필수 베이스라인 |
|---|---|---|---|
| ① 가격/수익률 방향 | LightGBM | Kronos / FinCast | naive, LightGBM |
| ② 변동성/거래량 | **LightGBM/CatBoost** 또는 TFT | Kronos·Chronos-2(주가채널) | **HAR-RV, GARCH-MIDAS**, EGARCH |
| ③ 외생피처 조건부 | TFT 또는 LightGBM | Chronos-2 / Moirai-2 | LightGBM(+외생피처) |

> 핵심: 실제 입력이 5종 이종 대체데이터(네이버·채용·DART·리포트·주가)이므로 **예측 본체는 트리/TFT**, 파운데이션 모델(Kronos·Chronos-2)은 **주가 채널 피처 생성기**로 stacking한다.

### 단계적 도입 로드맵 (제언 — 구현은 본 보고서 범위 밖)
1. **(a) point-in-time 피처 파이프라인**: 5개 소스를 일별 그리드 정합(공시/리포트는 발행시각 이후만), 장중캡처로 RV 계산.
2. **(b) 베이스라인 구축**: naive / HAR-RV / GARCH-MIDAS / LightGBM.
3. **(c) 피처 생성기 벤치**: Kronos·Chronos-2를 주가 채널에 zero-shot 적용 → 출력을 본체 입력 피처로.
4. **(d) 통합 본체 학습·비교**: LightGBM/CatBoost vs TFT를 워크포워드로 평가, 베이스라인 대비 유의성 검정.
5. **(e) 멀티에이전트 통합**: 검증된 예측을 **확률적 피처**로 signal-alpha 신호 체계에 합류(단독 매매 신호로 쓰지 않음).

> 배포 제약 고려: signal-alpha는 FastAPI 단일 백엔드 운영이므로, 동등 성능이면 **경량 모델(TTM, Kronos-mini/base, Chronos-2)** 을 선호. GPU 상시 점유가 부담되면 CPU 추론 가능 모델 우선.

---

## 9. 출처

- Kronos: *A Foundation Model for the Language of Financial Markets* — arXiv [2508.02739](https://arxiv.org/abs/2508.02739)
- FinCast: *A Foundation Model for Financial Time-Series Forecasting* — arXiv [2508.19609](https://arxiv.org/html/2508.19609v1)
- *Time Series Foundation Models for Multivariate Financial Time Series Forecasting* — arXiv [2507.07296](https://arxiv.org/html/2507.07296v1)
- Hugging Face 모델 카드: [google/timesfm-2.5-200m-transformers](https://huggingface.co/google/timesfm-2.5-200m-transformers), [NeoQuasar/Kronos-base](https://huggingface.co/NeoQuasar/Kronos-base), [amazon/chronos-2](https://huggingface.co/amazon/chronos-2)
- *The 2026 Time Series Toolkit: 5 Foundation Models* — [MachineLearningMastery](https://machinelearningmastery.com/the-2026-time-series-toolkit-5-foundation-models-for-autonomous-forecasting/)
- *Time Series Foundation Models: Strengths and Limitations* — [AI Horizon Forecast](https://aihorizonforecast.substack.com/p/time-series-foundation-models-a-deep)
- *Deep Learning & Transformer Architectures for Volatility Forecasting (US Equity)* — [MDPI JRFM 18(12) 685](https://www.mdpi.com/1911-8074/18/12/685)
- *Forecasting Realized Volatility using Temporal Fusion Transformers* — [IWQW DP 03/2023](https://ideas.repec.org/p/zbw/iwqwdp/032023.html)
- *Stock Volatility Prediction with Mixed-Frequency Data (GARCH-MIDAS + Transformer)* — arXiv [2309.16196](https://arxiv.org/pdf/2309.16196)
- *ML and Alternative Data to Predict Movements in Market Risk* — arXiv [2009.07947](https://arxiv.org/pdf/2009.07947)
- *Explainable deep learning for stock market trend prediction* — [PMC11577217](https://pmc.ncbi.nlm.nih.gov/articles/PMC11577217/)

---

## 부록: 범위 밖 (명시)

- 모델 다운로드 / 추론 / 파인튜닝 코드 작성 — 하지 않음.
- signal-alpha 코드 수정 · 엔드포인트 추가 — 하지 않음.
- 본 문서는 사양을 HF 모델 카드 / arXiv 원문과 대조해 작성된 **의사결정용 조사 보고서**다.
