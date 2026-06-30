# DataLab — 매출 재검정(R) + 유니버스 확대(A) + DART 이벤트스터디(B) (2026-06-30)

> worktree `sa-ml-longhorizon`. prod 읽기전용, 데이터 미커밋(연구), 도구만 브랜치 커밋.
> 한 줄: **검색→차기매출이 219종목 재검정서 robust 양성으로 뒤집힘**(39종목 강등 해소, permutation+BH-FDR 9/10 q≤0.012). 방향은 250종목서도 거래불가(미세 continuation). DART 사건→주가 이벤트스터디는 @21 무생존(검정력 부족, @250 후속).

## R — 검색 → 차기 분기 매출 재검정 @219 ★핵심
사용자 지적("매출값 없었던 것 아닌가") 확인 결과 **매출값은 실재**(dart_quarterly 602/609행 채워짐, 로더는 결측 드롭). R2(39종목)는 데이터가 아니라 **표본이 작아** permutation서 강등됐던 것 → **dart_krx250.csv(219종목·2015~23) + stockname_daily_krx250로 동일 파이프라인 재실행**(winsor[-0.9,3.0], R2 정본).

| 검정 | 결과 |
|---|---|
| 횡단면 IC lag 스윕 | 동시 +0.040(t1.81) / **lag1 +0.051(t2.73)** / lag2 +0.073(t2.70) |
| 부호 일관성 | 20/28분기 양수, LOO(분기) [+0.041,+0.055] |
| 횡단면 vs firm | pooled lead r≈+0.003 → **횡단면 효과**(또래대비), firm 시계열 아님 |
| **permutation+BH-FDR**(NPERM2000, predictor×lag×control 10셀) | **9/10 생존**, 헤드라인 abn lag1 raw p0.0010·q0.002; lag2 q0.002; 모멘텀통제(partial) lag2도 생존 |
| era 분할 | 2016~20 +0.019(t0.91, 무신호) / **2021~23 +0.100(t3.41)** |

- **판정: 검색 abnormal → 차기분기 매출YoY 횡단면 신호 = robust 양성.** 39종목 +0.107이 wide-null로 강등됐던 게 219종목서 타이트-null로 살아남(analytic t도 1.86→2.73 상승). **매그니튜드 외 처음으로 정직검정 통과한 신호.**
- ⚠️ 한계: (1) **매출**이지 주가 아님(사슬 search→매출→주가 중 1단계), (2) **횡단면**(상대), (3) **2021~23 era 집중**(2016~20 무신호 → 레짐 의존 가능), (4) name-search·KRX250.
- 방법 교훈: 횡단면 IC permutation은 **표본(종목수)** 에 강하게 의존 — 작은 유니버스의 강등이 큰 유니버스서 뒤집힐 수 있음. winsor 등 전처리 플래그 일치 필수(비윈저 시 +0.031로 약화).

## A1 — name-search 방향 @250 (231종목, 즉시)
`cross_sectional_attention.py --pit-features`:
- 횡단면 IC: 1주 +0.031(t4.83)·2주 +0.022(t3.44)·1개월 +0.017(t2.99) — 양수(반전 아닌 미세 continuation), 저-고 음수·tail 양수.
- **16모델 bake-off(PIT[abn,abn_mom], 73,503샘플): 어떤 모델도 다수 베이스라인 못 이김**(Dbase 전부 ≤0, best rankIC +0.014).
- **판정: 거래 가능한 방향 알파 아님.** 대형표본이라 미세효과(IC0.03)가 analytic-유의하나 경제적 무의미·ML 비활용. (큰-n 아티팩트 여부는 permutation 확인 여지 — 후속.)

## A2 — DART이벤트키워드 방향 @250 (미착수)
250 클린네임맵 → DART공시 수집(~1일) → 이벤트키워드 → DataLab 분할수집(~1,750콜·2-3일 resume) → 방향. 멀티세션. (@21은 직전 NULL.)

## B — DART 이벤트스터디(검색 우회) @21 → @250
신규 `scripts/dart_event_study.py`: 공시 사건(rcept_dt) → forward **KS11 시장조정 CAR**, 이벤트유형(~21종)별 + 종목내 일자셔플 permutation + BH-FDR.
- @21(13,792공시): **BH 생존 0/45**(유형×h). 원시 p<0.05 = 무상증자 h10 +5.6%(p0.012)·횡령 +2.6%(p0.043)·유상증자 +1.8%(p0.036) — 보정서 전멸. 소표본(무상증자 n8 등) underpowered.
- **판정 @21: 무생존**. 방향성 있어보이는 무상증자/횡령/유상증자는 250 확장(유형별 n↑)서 재확인 가치.

## 종합 / 다음
- **R이 헤드라인**: 검색→차기매출 robust 양성(219종목). 다음 = **사슬 완성: 매출-예측 검색이 주가까지 가는가**(실적 서프라이즈/PEAD 경유), era 견고성(왜 2021~23), 누수 재감사.
- A1 방향=거래불가 재확인. B는 @250 수집 후 재실행.

## 산출물 (도구 브랜치 커밋, 데이터 미커밋)
- 신규 `scripts/dart_event_study.py`. 수정 `scripts/scratch_r2_permutation_fdr.py`(--dart/--search-csv argparse).
- 데이터(미커밋): `dart_disclosures.json`, `dart_event_daily.csv`, `demand_daily.csv`, `kw_dart_event/` 등.

관련: `2026-06-29-datalab-direction-fundamental-closeout.md`(R2 원본) · `2026-06-30-datalab-direction-pit-event-retest.md` · [[attention-lead-lag-evidence]] · [[ml-bakeoff-datalab-result]]
