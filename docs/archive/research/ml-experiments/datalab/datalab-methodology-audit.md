# DataLab ML 테스트 방법론 감사 — 학술 문헌 교차검증 (2026-06-30)

> 딥 리서치(6각도·동료심사 논문/공식문서/리포지토리 25소스·101주장→25 적대적 검증, 21확정·4기각). 모든 근거 URL 포함.
> **한 줄 결론: 우리 방법론은 대체로 견고하고 "무조건부 방향 NULL"은 문헌상 예상된 결과다(검색=리테일 어텐션 프록시, 방향 효과는 조건부·부호반전). 단 3가지 진짜 갭이 있다 — (1) 소형·저유동·리테일 종목에 한정한 *조건부·반전(특히 패자)* 방향 테스트 미실시, (2) crash/tail-risk라는 검증된 *부호 있는* 타깃 미테스트, (3) 매그니튜드를 단독 방향이 아니라 기존 방향북의 *타이밍 오버레이*로 쓰는 구조 미검정(단 OOS 취약 경고). 진단 업그레이드(IC 시계열·purged CV·상호작용 모델링·검정력 보고) 권장.**

## 1. 우리 방법론에서 SOUND로 확인된 것

- **무조건부 방향 NULL = 예상된 결과.** 검색량(SVI)은 **리테일 어텐션 프록시**(Da-Engelberg-Gao 2011, J.Finance)라 방향 효과가 전 종목 균일하지 않고 조건부·부호반전 → 무조건부로 보면 상쇄돼 ≈0. 우리 NULL은 방법론 실패가 아님. (https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2011.01679.x)
- **매그니튜드(변동성/거래량) 양성 = 문헌 최강 합의.** "SVI는 키워드·지역·빈도 불문 변동성·거래량과 양의 관계"(DEG 2011; Financial Innovation 2024 리뷰; Lai 2022). 비방향 attention_spike 제품화는 정확히 합의와 일치. (https://link.springer.com/article/10.1186/s40854-023-00606-y)
- **BH-FDR 다중검정·walk-forward = 표준.** 신규 팩터는 t>3.0 + Bonferroni/Holm/BHY-FDR 필요(Harvey-Liu-Zhu 2016, RFS). 우리 규율 적정. (https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF)
- **PEAD에서 검색이 underreaction을 *줄인다*는 우리 발견도 한국·Naver 특정 논문과 정합.** "Investor Attention from Internet Search Volume and Underreaction to Earnings Announcements in Korea"(Sustainability 2020, **Naver DataLab 종목명 검색**): 어텐션이 PEAD 언더리액션을 *감소*. (https://www.mdpi.com/2071-1050/12/22/9358)
## 2. 진짜 갭 / 우리가 놓쳤을 수 있는 테스트 (근거 + 해야 할 검정)

### 갭1 ★ 조건부·반전 방향 (소형·저유동·리테일 + 패자 분리) — 우선순위 최고

- 우리는 방향을 **pooled/무조건부**로 테스트 → 효과가 리테일 지배 소형·저유동 종목에 집중되는데 전체 유니버스서 희석돼 NULL. 게다가 부호가 **반전(음수)**.
- 근거: Lai 2022(대만 TPEx, **GSVI 1~12주 음의 예측**, N48·소형·정보비대칭); **Eom & Park 2021(한국): 고어텐션 종목 유의한 음의 모멘텀, 거의 전적으로 과거 *패자* 반전이 주도(승자 지속 아님)**. (https://www.sciencedirect.com/science/article/abs/pii/S0275531921000258)
- **해야 할 테스트**: (a) 유니버스를 시총/유동성/리테일지분으로 분할 후 방향, (b) **부호 있는 반전 가설**(고어텐션 *숏*, 특히 과거 패자), (c) **패자 vs 승자 분리**(한국 효과는 비대칭이라 pooled가 가림).
- ⚠️ 전이 한계: Eom-Park·HPX는 어텐션 프록시가 *거래량*(검색 SVI 아님)·한국 모멘텀은 약함/반전 레짐 → "정합적이나 미검증 외삽".
### 갭2 ★ crash/tail-risk = 검증된 *부호 있는* 신규 타깃 (미테스트)

- 높은 리테일 검색 어텐션 → **미래 주가 *폭락(crash)* 위험을 양의 방향으로 예측**, 저유동·자연인(개인)지분 높은 종목서 강함. Chen&Chen 2024(J.Empirical Finance 75); 독립 확증 JFE 2021(검색→폭락위험 ~19%↑, 준자연실험). (https://www.sciencedirect.com/science/article/abs/pii/S0927539823001238 · https://www.sciencedirect.com/science/article/abs/pii/S0304405X21000933)
- **해야 할 테스트**: NCSKEW/DUVOL 또는 극단음수수익 지표를 타깃으로 검색→폭락위험, **유동성·개인지분 조건부**. 우리 매그니튜드 신호가 이미 통하는 리테일 세그먼트와 정합 → 우리 3타깃(방향·매그니튜드·매출)에 빠진 **부호 있는 꼬리위험**.
### 갭3 매그니튜드→방향 "타이밍 오버레이" 구조 (단 OOS 취약 경고)

- 매그니튜드 신호의 검증된 가치는 **단독 방향이 아니라 기존 방향북의 노출을 스케일/게이트하는 타이밍**(Moreira-Muir 2017, 분산 역가중 → Sharpe↑). (https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513)
- ⚠️ **강한 경고(적대적 패널 기각)**: 순진한 vol-managed는 **OOS서 unmanaged 못 이김**(Cederburg et al. 2020, JFE 138). in-sample spanning alpha는 실거래 불가. → "검색→예측변동성/거래량으로 기존 모멘텀/반전북을 게이트"는 **실시간·거래비용 포함 walk-forward로만** 검정(우리 OOS 규율 유지). (https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X)
## 3. 진단 업그레이드 (false-negative 방지)

- **IC 시계열 진단**: 단일 pooled IC 대신 **per-period 횡단면 IC의 평균·표준편차·t + 레짐별 부호반전** 점검(Ding 2010 fundamental law: IR ∝ 평균IC·√N, ∝ 1/IC변동성). pooled ≈0이 "죽은 신호"인지 "부호반전 신호"인지 구분. ⚠️ 단 적대적 패널은 "strategy-risk 수학만으로 ≈0이 신호 은폐를 *증명*"은 **기각** — 돌려볼 *진단*이지 결론 아님. (https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1625834)
- **상호작용 모델링**: ML 이득은 주로 **비선형 *상호작용***서 옴(Gu-Kelly-Xiu 2020, RFS). 상호작용에만 사는 방향신호는 선형/비상호작용 모델엔 *안 보임*. 도구: sklearn **PDP/ICE**(상호작용 탐지, https://scikit-learn.org/stable/modules/partial_dependence.html), **HistGradientBoosting `interaction_cst`**(상호작용 제약, https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
- **누수/중첩 윈도우**: 우리는 비중첩+walk-forward였으나 더 엄밀한 표준은 **purging**(라벨구간 겹치는 학습표본 제거). sklearn **TimeSeriesSplit**(https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), mlfinlab/skfolio **CombinatorialPurgedCV**(https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html).
- **검정력 보고**: FDR은 false-positive를 막되 검정력을 깎음 → **효과크기·검정력을 FDR p와 함께 보고**, 조건부 가설(리테일·소형·반전)은 전체 키워드 multiplicity에 묻지 말고 소수 이론기반으로 사전등록.
## 4. 기각된 주장 (쫓지 말 것)

- vol-managed Sharpe 이득이 조건부 알파 증명 → **기각(0-3, in-sample 한정)**.
- GSVI 양의쇼크 분해 → 방향 예측 → **기각(1-2, 약함)**.
- strategy-risk 수학이 ≈0 pooled IC의 신호 은폐를 증명 → **기각(0-3, 진단일 뿐)**.
- DEG 강독해(SVI가 2주 방향-후-반전 깔끔히 예측) → **기각(1-2)**.
## 5. 권장 다음 테스트 (우선순위)

1. **조건부 반전 방향**(갭1): KOSDAQ 소형·저유동·고개인지분 부분집합서 고어텐션 *숏*(패자 분리) 부호테스트 + IC 시계열 진단. *기존 cross_sectional_attention 재사용 + 종목 분할만.*
1. **crash/tail-risk 타깃**(갭2): NCSKEW/DUVOL 신규 타깃, 검색→폭락위험 조건부. *신규 라벨 + 기존 가격데이터.*
1. **IC 시계열 + 상호작용 진단**(전 신호 재점검, 저비용).
1. 매그니튜드 타이밍 오버레이(갭3)는 기존 방향북 필요 + OOS 취약 → 후순위.
## 전이 한계 / 주의 (감사가 명시)

- 조건부 결론 최강 근거(HPX·Eom-Park)는 **거래량** 어텐션이지 검색 SVI 아님 → 외삽. 실제 검색량은 Lai(대만)·crash 논문만.
- 한국은 약모멘텀/반전 레짐이라 미국/대만 부호가 그대로 전이 안 될 수 있음. Eom-Park 반전은 패자주도·특이.
- vol-timing OOS 비판이 현 합의(Cederburg 2020) → 옛 Moreira-Muir in-sample 낙관 무시.
- **우리 BH-FDR가 *우리 특정* false-negative를 냈는지는 문헌이 직접 답 못 함 → 자체 검정력 분석 필요.**
