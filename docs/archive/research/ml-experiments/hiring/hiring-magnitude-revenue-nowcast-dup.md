# 채용(HIRING) — 매그니튜드 & 차기 매출 나우캐스팅 검정 (2026-06-30)

> 채용 단독 *방향(↑/↓)* 알파는 기각됐다(universe57, 2026-06-29). 지금까지 검정은 전부 방향만
> 타깃이었다. 이번엔 한 번도 안 본 두 타깃을 검정한다: ① **매그니튜드**(움직임의 크기/변동성),
> ② **차기 분기 매출 나우캐스팅**. 유니버스·하니스·BH-FDR은 universe57과 동일.
> 
> **한 줄 결론: 매그니튜드는 방향과 마찬가지로 무신호(BH-FDR 생존 0). 매출 나우캐스팅은
> *시사적이지만 미확정* — knn rankIC_xs +0.125(raw p=0.015), 전 비선형 모델이 양(+)·전 선형
> 모델이 음(−)으로 일관된 비선형 구조를 보였으나 최종 BH-FDR(N=25)은 못 넘김(best q=0.354).
> 단독 채용 실험 중 가장 강한 결과 — 방향·매그니튜드처럼 죽지 않은 유일한 리드.**

---

## 1. 공통 설정

- 유니버스: KOSPI 채용 충분 57종목(universe57과 동일, precise-rematch 6,317공고).
- 채용 피처: volume(계절보정 플로우 모멘텀·yoy·recency) ± duty(기술직군 비중). PIT(as_of 이하).
- CV: 확장창 워크포워드(날짜경계 누수차단). 판정지표: per-date 횡단면 rankIC(`rank_ic_xs`).
- 유의성: 라벨셔플 permutation(200) → Benjamini–Hochberg FDR(`stats.py`).
- 신규 하니스: `magnitude.py`(타깃·횡단면 라벨러)·`fundamentals_dart.py`(OpenDART 리더)·
`fundamentals_dataset.py`(매출 데이터셋)·`bakeoff --target/--source revenue`. 단위테스트 23 GREEN.

## 2. Track A — 매그니튜드 (움직임의 크기)

**타깃**: ① `|초과수익|`(시장대비 움직임 크기) ② `realized_vol`(forward 실현변동성). y는 per-date

횡단면 "큰 움직임 vs 작은 움직임" 이진, 연속 매그니튜드를 rankIC로 채점. horizon 5·10·20·30·60.

**결과(fast-6 permutation, N=60 = 2타깃×5h×6모델): BH-FDR 생존 0.** 최강 셀 abs_return h5

logistic +0.059(raw p 0.010·**BH_q 0.600**), realized_vol h20 knn +0.050(p 0.035·q 0.612). 인접

horizon 붕괴(전형적 노이즈). → **매그니튜드도 방향과 동일하게 무신호.** 학술적으로 어텐션→

변동성 효과는 소형·리테일 집중이라 KOSPI 대형주에선 약함([[attention-lead-lag-evidence]]),

universe57 §7-bis(공고일 변동성 무증가, vol_post/pre≈0.987)와도 일관.

## 3. Track B — 차기 분기 매출 나우캐스팅

**가설**: 채용 패턴이 *주가 방향*이 아니라 *실제 매출*을 선행/동행하는가("채용→사업확장→매출").

**데이터**: OpenDART `fnlttSinglAcntAll` 분기 매출을 우리가 직접 인출(연구 전용 자급자족 리더,

팀 DART 수집기·prod 미의존; 사용자 승인 스코프 예외). 57종목×2016–2024 = **1,813 분기행**.

- ⚠️ 데이터 함정 2건 처리: (a) 중간보고서 `thstrm_amount`=3개월·`thstrm_add_amount`=누적 →
누적 차분; (b) 공시일은 `rcept_no[:8]`로 복원해 PIT 라벨(분기말 as_of < known_at).

삼성 검증: 2022 분기합 302조·2023 259조 = 실제와 일치.

**타깃**: 분기말 as_of 채용 피처 → 그 분기 **YoY 매출성장** 횡단면 랭킹(상위 절반=고성장).

표본 620(54종목·28분기).

**디스크립티브(전 16모델)**:

| 모델 | rankIC_xs | 비고 |
| --- | --- | --- |
| **knn** | **+0.125** | sd 0.099(평균>sd, 안정) |
| random_forest | +0.099 |  |
| stacking | +0.090 |  |
| grad_boost | +0.084 |  |
| voting_soft | +0.080 |  |
| hist_grad_boost | +0.073 |  |
| 선형(lda/ridge/logistic) | **−0.01~−0.02** | 비선형 모델과 부호 반대 |

**유의성(permutation 200 + BH-FDR, N=25 = volume 13모델 + duty 6 + volume+duty 6)**:

- **BH-FDR 생존 0** (최강 knn **BH_q=0.354**).
- 단 raw p 상위 8셀이 **전부 비선형/트리 모델**: knn 0.015·stacking 0.040·grad_boost 0.055·
RF 0.060·voting 0.085·HGB 0.115·extra_trees 0.120. knn·stacking은 무보정 p<0.05.

- **모든 비선형 모델 양(+)·모든 선형 모델 음(−)** = best-of-노이즈(방향연구의 고립 horizon
픽)와 다른 **일관된 비선형 구조**. 25셀 중 기대 위양성 ~1.25, 관측 2(knn·stacking).

**판정: 시사적이지만 미확정.** 다중검정을 못 넘으므로 "확정 신호" 아님. 그러나 (i) 단독 채용

전 실험 중 최강(+0.125), (ii) 비선형/선형 부호 분리의 구조적 일관성, (iii) 평균>sd 안정성 →

**방향·매그니튜드처럼 죽지 않은 유일한 리드.** 표본(620)·피처가 빈약한 1차 패스임을 감안.

## 4. 다음 레버 (매출 나우캐스팅만)

- **표본 확대**: 분기(28개)는 횡단면 통계력의 병목. 유니버스 확대(KOSPI200 전체 매출 인출)·
월별 신호화로 표본↑.

- **피처 확대**: 비선형 우위 = 단순 3피처(flow/yoy/recency)에 신호가 압축됨. duty 세분·OCR
스킬·섹터 수요 등 추가 피처로 비선형 모델에 먹이.

- **다중소스 융합**(채용+특허+DataLab → 매출): 이미 `fusion_db.py` 존재; 매출 타깃으로 확장.
- 누군가 `fundamentals_dataset.build_patent_revenue_dataset`(특허→매출)를 추가함 → 특허 매출
나우캐스팅도 동일 하니스로 가능.

## 5. 한계

- 정정공시 덮어쓰기(최신본만) → 원시 vintage 누수 약간 가능(보수적). 표본 620 소규모.
- 무거운 모델 permutation은 volume 피처에만(duty/both는 fast-6). 단 최강 knn은 fast-6 포함.
## 부록 — 재현

- 코드(미커밋, worktree `sa-hiring-ml`/`research/hiring-ml-phase45`): `app/ml/research/`
{magnitude,fundamentals_dart,fundamentals_dataset}.py + bakeoff `--target`/`--source revenue`,

tests/{test_ml_magnitude,test_ml_fundamentals}.py (+기존 = 74 GREEN).

- 스윕: `C:\Users\804\Documents\ML\Hiring\` run_magnitude_sweep.sh·run_revenue_sweep.sh·
run_revenue_heavy.sh, 산출 mag_fdr.txt·rev_fdr_final.txt·revenue_dart.csv.
