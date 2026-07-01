# DataLab — crash/tail-risk(②) + SUE-PEAD 경제성(③) (2026-07-01)

> worktree `sa-ml-longhorizon`. prod 읽기전용, 데이터 미커밋(연구), 도구만 브랜치 커밋.
> 한 줄: **②검색→미래 폭락위험(NCSKEW/DUVOL/CRASH) 무신호**(krx250 넓은표본 0/21, 고개인 셀 오히려 음부호; KOSDAQ은 블록4개로 검정불가). **③SUE-PEAD 롱숏은 20일 비용후 사망(BE 37bp·t1.33)이나 40일선 비용 견딤(BE 109bp·era 양쪽 양수)·단 t≈1.9로 검정력 부족** = 한계적·호라이즌 민감, 트레이더블 확정 아님. 딥리서치 감사 갭 ②③ 종결.

## ② crash/tail-risk — 검색 → 미래 폭락위험
### 방법
firm-specific 주간수익 W=ln(1+ε), ε=KS11 시장모델(±1 lead/lag, full-sample β) 잔차. 비중첩 26주(기본)/13주 블록마다 **NCSKEW**·**DUVOL**·**CRASH**(Hutton 3.09σ dummy) 라벨. 예측자=선행 블록 PIT abnormal 검색 평균. 횡단면 IC(라벨×셀), within-block 셔플 permutation+BH-FDR, 시총·유동성·개인(프록시) tercile 조건부. `scripts/scratch_crash_risk.py`(신규). 근거 Chen&Chen 2024 JEF·Chen-Hong-Stein 2001.

### 결과
| 표본 | 블록 | BH 생존 | 요지 |
|---|---|---|---|
| KRX250 | 26w(14) | **0/21** | 전 셀 \|t\|<2.3. large NCSKEW/CRASH 한계 음(−1.9/−2.2), **hi-retail 음부호**(가설 반대) |
| KRX250 | 13w(30) | **0/21** | 전 셀 \|t\|<1.5. hi-retail DUVOL −0.032(t−1.25) — 여전히 음 |
| KOSDAQ | 26w(4) | 0/20 | **블록 4개 = 검정력 없음.** 몇몇 고t(CRASH large +0.34 t3.46)는 nblk=4 아티팩트 |

### 판정: 무신호
- **위치·부호 반증**: 검정력 있는 KRX250서 어떤 라벨·셀도 BH 생존 0. 가설상 양(+)이어야 할 고개인·저유동 셀이 오히려 **음(−) 부호**(검색이 폭락위험을 낮춘다? = 노이즈).
- **서식지 표본 검정불가**: KOSDAQ은 8년/26주면 블록쌍 4개뿐 → t-stat 무의미. 소형 유니버스 대폭 확대 없이는 crash 라벨 검정 자체가 불가.
- **결론: 검색은 미래 폭락위험(방향성 꼬리risk)을 예측하지 않음.** 검색의 트레이더블 가치는 대칭 매그니튜드(vol/volume)+매출 나우캐스트로 확정된 지형 그대로. [[datalab-revenue-nowcast-pead]]

## ③ SUE-PEAD 경제성 — 거래비용 반영 롱숏
### 방법
분기 실적공시(잠정) 매칭 7,625건. **분기 decile 롱숏**(상위10% SUE − 하위10%, 동일가중, drift창 보유) → 분기 L-S 수익 시계열. gross 평균·분기 t·IR·hit·era분할, **비용 스윕**(per-side 0/15/30/50bp, 분기 리밸=양다리 enter+exit=4×c/분기)·breakeven·decile 단조성. SUE 3정의 비교. `scripts/sue_economics.py`(신규, `search_pead_surprise.revenue_sue` 재사용).

### 결과 (KRX250)
| drift | 변수 | gross%/분기 | t | IR | hit | 16-20 | 21-23 | BE(bp/side) | net_t@30bp |
|---|---|---|---|---|---|---|---|---|---|
| 20d | SUE_accel | +1.48 | 1.33 | 0.27 | .54 | +0.24 | **+2.73** | 37 | 0.25 |
| **40d** | **SUE_accel** | **+4.35** | **1.91** | 0.39 | .67 | **+3.06** | **+5.63** | **109** | 1.39 |
| 60d | SUE_accel | +4.79 | 1.29 | 0.27 | .48 | +0.43 | +9.56 | 120 | 0.97 |
| 40d | SUE_yoy(level) | +3.88 | 1.88 | 0.36 | .56 | +3.44 | +4.43 | 97 | 1.29 |
| 40d | search_abn | +2.80 | 2.81 | 0.52 | .66 | +3.22 | +2.22 | 70 | 1.61 |

- **비용 스윕(20d SUE_accel)**: 0bp +1.48 → 30bp +0.28(t0.25) → 50bp **음수**. 20일은 breakeven 37bp라 현실 왕복비용서 사망.
- **decile 비단조**(20d): D1~D9 지지부진, **D10만 +2.32**(소수 고SUE 주도) — 클린 그래디언트 아님.

### 판정: 한계적·호라이즌 민감 (트레이더블 확정 아님)
1. **20일 = 비용후 사망**: gross t1.33, net_t@30bp 0.25, era 전부 2021-23 집중(2016-20 +0.24). 메모리의 "경제성 약함·비용후 marginal·2021~23의존" 수치 확증.
2. **40일 = 비용은 견디나 검정력 부족**: gross +4.35%(t1.91<2), **BE 109bp**로 비용 여유 상회, **era 양쪽 양수**(더 robust). 그러나 분기 t·net_t(1.39) 모두 <2, 24분기 소표본. = 실재 가능성 있으나 t>2 확정 불가.
3. **정의 취약·호라이즌 민감**: SUE_yoy는 era 패턴 다름, 60일은 노이즈·편중. search_abn 40d gross t2.81은 검색이 SUE를 나우캐스팅해 겹치는 것(독립 알파 아님, 기존 결론 [[datalab-revenue-nowcast-pead]]).

**결론: SUE-PEAD는 20일 비용후 사망, 40일선 비용 견디는 한계신호(t≈1.9). 확정 트레이더블 알파 아님.** 유일한 개선 여지 = **더 많은 분기/종목(KOSPI200+) 또는 컨센서스 기반 SUE**로 40일 신호를 t>2로 확정할지. 현 증거로는 나우캐스트/리서치 레이어.

## 종합 (감사 갭 ①②③ 종결)
DataLab 3대 후속 모두 종결: ①조건부 반전=NULL, ②crash위험=무신호, ③SUE-PEAD=비용후 한계. **검색의 확정 가치지형 불변**: 대칭 매그니튜드 흔적 + 차기매출 level 나우캐스트, 트레이더블 방향/꼬리/PEAD 알파 아님.

## 한계 / 다음
- ② KOSDAQ crash는 표본길이(8년)·소형유니버스(50) 한계 — 수백 종목 확대 시에만 재검 가치. ③ 40일 t≈1.9는 KOSPI200 확대나 컨센서스 SUE가 다음 레버.
- 산출물: `scripts/scratch_crash_risk.py`, `scripts/sue_economics.py`. 데이터/CSV 미커밋. **머지 보류**([[research-tooling-no-merge-until-signal]]).
