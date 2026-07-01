# DataLab — 조건부 반전 방향 (audit gap ①) (2026-07-01)

> worktree `sa-ml-longhorizon`. prod 읽기전용, 데이터 미커밋(연구), 도구만 브랜치 커밋.
> 한 줄: **조건부(리테일 서식지)로도 방향 반전은 트레이더블 신호로 부활하지 않음.** 유의미한 반전은 리테일 서식지가 아니라 **유동성 높은 대형주 단기 반전**(krx250 liquid, t≈−2.9)에 있어 리테일-어텐션 메커니즘과 반대이고, **두 유니버스 42셀 다중검정 정정 시 0개 생존**·t<3·h20 소멸. → 무조건부 방향 NULL 결론 유지.

## 배경
무조건부 DataLab 방향은 전키워드 NULL(정본 [[datalab-revenue-nowcast-pead]]). 문헌상 예상 — 검색=리테일 어텐션이라 방향효과는 조건부·부호반전이라 pooling 시 상쇄. 딥리서치 감사 갭①: "**소형·저유동·고개인지분(리테일 서식지)에서 고어텐션→반전(하락), 특히 패자 주도**가 살아나는가?" (Eom&Park 2021 KR, Lai 2022 TW, Da/Engelberg/Gao 2011).

## 방법
- 예측자 = PIT abnormal name-search(trailing rolling-z 60d), 타깃 = 미래 h일 **횡단면 초과수익**(셀 내 demean). 반전 ⇒ **IC<0**.
- 셀 = ALL + {시총, Amihud 비유동성, 개인} 각 축의 tercile(하/상), 각 셀을 과거 h일 수익 부호로 **패자/승자 재분할**. h=5/10/20일(≈1/2/4주).
- 엄밀성: **비중첩 주간 per-period IC 평균·t**(독립주), **within-date 셔플 permutation**(NPERM2000), **BH-FDR**(셀×horizon), **within-firm(종목 demean) IC_wf**(정적특성 vs timing 가름 — 07-01 교훈 [[patent-volatility-magnitude-signal]]).
- 도구: `scripts/scratch_conditional_reversal.py`(신규), 재사용 `search_to_magnitude.load_px/rolling_z/ffill`·`event_study_leadlag.pearson`.

### ⚠️ 개인(리테일) 축 = 프록시 (실데이터 소싱 실패)
실제 종목별 개인 보유/거래비중 역사패널(2016–23)을 시도했으나 이 환경서 **이중 차단**: (1) pykrx 투자자 엔드포인트가 KRX 로그인(KRX_ID/KRX_PW) 요구, 사용자는 **소셜로그인이라 비밀번호 없음**, (2) 엔드포인트 자체가 JSON 아닌 **HTML 차단 응답** 반환([[fdr-stocklisting-snapshot-broken]] 계열). → 개인 축을 **저가주(낮은 명목주가)+회전율(일거래대금/시총) 합성 프록시**로 조작화(Kumar 2009: 개인은 복권형 저가주 선호 — DataLab 검색 군중과 정합; 학술상 개인지분보다 표준적 프록시). 실KRX 수집 도구 `scripts/collect_investor_share.py`는 스모크모드 포함해 보존(창 확보 시 재사용).

## 결과

### KOSDAQ 소형주 (46종목, 진짜 서식지 · retail=프록시)
| 셀 | h | IC | t | perm_p | BH_q | IC_wf |
|---|---|---|---|---|---|---|
| illiquid | 5 | −0.040 | −1.53 | 0.084 | 0.77 | −0.042 |
| illiquid | 10 | −0.049 | −1.27 | 0.148 | 0.77 | −0.046 |
| lo-retail | 10 | −0.046 | −1.30 | 0.152 | 0.77 | −0.047 |
| lo-retail | 20 | −0.062 | −1.14 | 0.186 | 0.77 | −0.057 |
| hi-retail | 5 | −0.022 | −0.71 | 0.404 | 0.77 | −0.020 |
- **BH 생존 0/21.** 저유동·소형 셀 IC는 반전 방향(음수)이나 전부 t<1.6·perm 미유의(n=46 소표본).
- **패자/승자(hi-retail)**: 예측 부호 선명 — h10 losers 고어텐션 xs **−1.58%** vs winners **+1.30%**; h20 losers **−4.19%**(n_hi=9) vs winners **+1.98%**. pooled_corr losers −0.06/winners +0.08~+0.22. **방향은 문헌대로지만 고어텐션-패자 관측이 극소(n_hi 9~14).**

### KRX250 (238종목, 넓은 표본 · 시총 tercile 대조)
| 셀 | h | IC | t | perm_p | BH_q | rej | IC_wf |
|---|---|---|---|---|---|---|---|
| **liquid** | 5 | −0.022 | −2.88 | 0.0015 | 0.026 | **YES** | −0.023 |
| **liquid** | 10 | −0.027 | −2.91 | 0.0025 | 0.026 | **YES** | −0.029 |
| liquid | 20 | −0.018 | −1.27 | 0.171 | 0.71 | | −0.016 |
| small | 10 | −0.015 | −1.27 | 0.100 | 0.58 | | −0.017 |
| illiquid | 5 | +0.008 | +0.96 | 0.202 | 0.71 | | +0.007 |
| hi-retail | 5/10/20 | −0.003/−0.003/+0.021 | <1.3 | ns | | | |
- **BH 생존 2/21 — 전부 `liquid`(유동성 高) 셀**, within-firm도 유지. **hi-retail·illiquid·small은 무신호.**
- 즉 넓은 표본의 유의 반전은 **리테일 서식지가 아니라 대형·유동주 단기 반전** — 리테일-어텐션 가설과 **반대 셀**.

## 판정
**조건부 반전 가설 기각 (트레이더블 방향 신호 아님).**
1. **위치 반증**: 넓은 표본(krx250)의 유의 반전은 고개인·저유동이 아니라 **liquid 대형주**. 리테일 어텐션 메커니즘이면 hi-retail에 있어야 하나 hi-retail=NULL. → 대형주 유동성공급·과잉반응 미시구조 반전(검색과 무관, 검색이 가격을 뒤늦게 좇는 것에 가까움).
2. **다중검정서 소멸**: 두 유니버스 42셀을 한 trial 패밀리로 보면 BH/Bonferroni 문턱 0.05/42≈**0.00119** < liquid p(0.0015, 0.0025) → **0개 생존**. 단일 유니버스 국소 유의가 전체 탐색 보정서 사라짐.
3. **t<3·호라이즌 불일치**: liquid도 t≈−2.9(감사 Deflated-Sharpe 기준 t≥3 미달), h20서 소멸.
4. **소표본 서식지**: KOSDAQ hi-retail 패자에서 예측 부호(−4.2%)는 관측되나 n_hi=9 — 방향 정합·검정력 부재. 가설이 살아있다면 **소형 유니버스 확대(수백 종목)** 가 필요하나, 현 증거는 부활 근거로 불충분.

**결론: 무조건부 방향 NULL 유지. 조건부(리테일 서식지 프록시)로도 DataLab 검색의 트레이더블 방향/반전 알파는 부활하지 않음.** DataLab 가치는 매그니튜드 흔적 + 차기매출 나우캐스트로 확정된 지형 그대로.

## 한계 / 다음
- 개인 축 = 프록시(저가주+회전율), 실개인지분 아님 — KRX 유료/PW멤버십 확보 시 재검. 프록시가 서식지를 잘못 짚었을 소지는 낮음(저가·고회전은 강한 리테일 프록시).
- 소형주 유니버스 46은 tercile 검정력 한계. **감사 갭② crash/tail-risk·③ SUE-PEAD**는 이 결과(방향 종결)와 독립 → 다음 세션.
- 산출물: `scripts/scratch_conditional_reversal.py`, `scripts/collect_investor_share.py`(실KRX 소싱·보존). 데이터/CSV 미커밋. **머지 보류**([[research-tooling-no-merge-until-signal]]).
