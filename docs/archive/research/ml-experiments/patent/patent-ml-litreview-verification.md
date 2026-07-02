# 특허 ML 검증 — 딥리서치 + 최종 검증 결과 (문헌 교차검증)

**일자**: 2026-06-30~07-01 · **분기**: `research/patent-magnitude-revenue-fusion` · **데이터**: 로컬 docker DB(실특허 82,869건, 13종목) + FDR 주가 + DART 실매출

---

## 0. 한 줄 결론

특허 활동 → 차기 실현변동성의 **횡단면 연관은 통계적으로 확고**(permutation 1000회 p≈0, BH 9/10, 로버스트니스 5/5)하나, **within-firm 분해 시 붕괴** → **"정적 종목 특성(R&D 집약주가 늘 고변동)"이지 "시점 timing 알파"가 아님**. 딥리서치 결과, 우리 검증엔 **신호를 놓칠 구멍 4개 + 빠진 표준절차 4개**가 있으며, 가장 큰 건 **피처(건수 vs 가치)**와 **유니버스(대형주만)**.

---

## 1. 최종 검증 결과 Matrix

| 검증 단계 | 무엇을 봤나 | 결과 | 해석 |
| --- | --- | --- | --- |
| **횡단면 bake-off** (정적 포함) | rankIC_xs 13종목·36분기 | +0.15~0.29(12모델 전부 +) | 연관 존재 |
| **permutation 1000회** | 우연이면 이만큼? (라벨셔플) | p≈0.000~0.003, **BH 9/10 생존**(top4는 Bonferroni도 통과) | **우연 아님(확고)** |
| **로버스트니스** (h=5·20·60, lb=90·180·365) | 설정 바꿔도 유지되나 | 5/5 설정 전부 +, 긴 윈도서 강화(+0.36~0.38) | 과적합 아티팩트 아님 |
| **within-firm 분해** (종목 고정효과 제거) | timing만 남기면? | rankIC_xs +0.14~**−0.14** 붕괴·부호 엇갈림 | **timing 무신호 = 정적 특성** |
| 세 라벨 비교 | 방향/매그니튜드/매출 | 방향 기각·**변동성만 신호**·매출 무신호 | 크기에만 신호 |

**종합 판정**: *통계적으로 견고한 횡단면 "변동성 특성"(리스크모델 입력엔 유효), 시점 알파·방향 알파는 아님.*

---

## 2. 우리 접근은 문헌과 맞나? → 맞음

- **Da·Engelberg·Gao (2011, J.Finance)** *In Search of Attention*: 리테일 어텐션(검색량)은 **변동성·거래량 예측, 장기 수익 방향은 못 맞힘**. → 우리 "방향 기각·변동성 신호"는 문헌 합의와 일치. **방향 신호를 놓친 게 아니라, 방향엔 원래 신호가 약함.**
- **Moreira·Muir (2017, J.Finance)** *Volatility-Managed Portfolios*: **변동성 예측은 경제적 가치 있음** — vol-timing으로 샤프 ~25%↑, 알파 4.9%. 활용법은 *"매그니튜드→방향"이 아니라 "매그니튜드→포지션 사이징"*.

---

## 3. 신호를 놓칠 수 있는 구멍 (중요도순)

### 🔴 (a) 피처: 특허 *건수* → 특허 *가치* — 가장 큰 누락

**Kogan·Papanikolaou·Seru·Stoffman (2017, QJE)**: 수익 예측 특허 측정치는 건수가 아니라 **경제적 가치**(특허 공시 **주가 반응** + **피인용** 가중). 우리는 순수 건수·모멘텀만 사용 → 잘못된 피처로 신호를 놓쳤을 수 있음. 공개 데이터(KPSS GitHub) 차용 가능.

### 🔴 (b) 유니버스: 대형주 13종목만 → 효과 집중되는 소형/리테일주 미포함

Da et al.: 어텐션 효과는 **소형·리테일 지배·차익거래 어려운 종목에 집중**. 우리 유니버스는 효과가 가장 약한 곳 → false-negative.

### 🟠 (c) 검정력 부족: 13×36 소표본 → 약한 진짜 신호 놓침

### 🟠 (d) 과도한 중립화가 팩터 신호 제거 가능 (within-firm 0만 보면 팩터 놓침)

---

## 4. 빠진 표준 검정 절차

| 절차 | 현황 | 근거 |
| --- | --- | --- |
| Purging + Embargo CV (`TimeSeriesSplit(gap=)`) | ❌ — horizon 20이라 경계서 라벨 겹침→누수(변동성 신호 *과대평가* 가능) | López de Prado / sklearn |
| Combinatorial Purged CV (다중 경로) | ❌ 단일 경로만 | López de Prado |
| Deflated Sharpe / 시도횟수 보정 | ❌ 3라벨×5설정×16모델 미보정 | López de Prado / Harvey·Liu·Zhu |
| 다중검정 t≥3.0 | △ BH-FDR은 함 | Harvey·Liu·Zhu (2016) |
| BH-FDR · within-firm 분해 | ✅ 우리가 잘한 부분 | — |

---

## 5. 다음 단계 (구체 · 우선순위)

1. **embargo 적용 재검정** — `walk_forward_folds`에 gap(≥horizon) 추가 후 변동성 신호 재확인(과대평가 여부). → sklearn `TimeSeriesSplit(gap=)`
1. **특허 *가치* 피처** — KPSS 시장반응 특허가치 차용(건수→가치), within-firm timing 재검정
1. **소형/리테일주 유니버스 확대** — 효과 집중 구간 검정
1. **변동성 신호 경제가치** — vol-managed 오버레이 샤프 검정(Moreira·Muir)
1. **시도횟수 보정** — 전체 trial로 Deflated Sharpe / t≥3

---

## 6. 출처 (동료심사·공식문서·공식 GitHub만)

- Da, Engelberg, Gao (2011) *In Search of Attention*, JF — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2011.01679.x · https://www3.nd.edu/~zda/google.pdf
- Moreira, Muir (2017) *Volatility-Managed Portfolios*, JF — https://www.nber.org/papers/w22208
- Harvey, Liu, Zhu (2016) *…and the Cross-Section of Expected Returns*, RFS — https://academic.oup.com/rfs/article/29/1/5/1843824 · https://www.nber.org/papers/w20592
- Kogan, Papanikolaou, Seru, Stoffman (2017) QJE — https://academic.oup.com/qje/article-abstract/132/2/665/3076284 · https://github.com/KPSS2017/Technological-Innovation-Resource-Allocation-and-Growth-Extended-Data
- López de Prado, *10 Reasons Most ML Funds Fail* — https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf
- scikit-learn `TimeSeriesSplit` (gap=embargo) — https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
