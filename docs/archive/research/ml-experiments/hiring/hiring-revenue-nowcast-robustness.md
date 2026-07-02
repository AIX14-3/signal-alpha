# 채용→매출 나우캐스팅 — 견고성 검증(월별 신호화 + purge) (2026-07-02)

> 채용→차기분기 매출 YoY 나우캐스팅 신호는 94종목서 BH-FDR 생존했으나 117 확대서 미생존이라
> "marginal"로 남았다([2026-06-30 실험](2026-06-30-hiring-magnitude-revenue.md)). 이 신호가
> (a) 데이터누수 아티팩트인지, (b) 표본 부족(분기 28개)으로 취약한지를 **purge(embargo) + 월별
> 신호화**로 검증한다.
> 
> **한 줄 결론: 신호는 진짜다. 세 독립 검증(94 분기별 · 94 purge=embargo · 94 월별=표본3배) 모두
> BH-FDR 생존 2셀로 일관. 누수 아티팩트 아님(purge 통과), 표본 취약 아님(월별 횡단면 28→86서도
> 생존). rich(AI/HW/SW 직군세분) 피처가 지배적. 단 효과는 modest(rankIC ~0.07–0.12)이고 유니버스
> *데이터 품질*에 민감(clean 94 생존·thin 117 희석) — "견고한 modest 신호"이지 "완성 알파" 아님.**

---

## 1. 배경 / 검증 설계

- 살아있는 유일 단독-소스 리드: 채용 피처(분기말 PIT) → 그 분기 YoY 매출성장 횡단면 랭킹.
- 두 의문: **누수?** / **표본취약?** → 두 검증:
1. **purge(embargo=1)**: 방법론 문헌([2026-07-01 리뷰](2026-07-01-altdata-ml-methodology-review.md))
상 누수는 *거짓 양성*만 만든다 → purge 후에도 생존하면 누수 아님.

1. **월별 신호화**: 분기당 신호일 1개(분기말)→3개(3개월 말일). 표본 734→2,232·횡단면 28→86.
분기내 3개월은 라벨 공유 → **embargo=3**로 경계 누수 차단. (하니스 `--embargo`, `--revenue-signal-freq`)

- 유니버스: **깨끗한 94종목**(채용 ≥15공고·2년; 117은 얇은 데이터가 희석시켜 제외). fast-6 +
트리앙상블 permutation(200). feature-set volume·rich(volume+AI/HW/SW 직군 share).

## 2. 결과 — 세 검증 모두 BH-FDR 생존 2셀

| 검증 | 표본 | 횡단면 | 생존셀(BH q<0.05) |
| --- | --- | --- | --- |
| 94 분기별(원) | 734 | 28 | decision_tree q0.00 · knn q0.05 |
| 94 + **purge**(embargo=1) | 734 | 28 | **knn q0.00 · decision_tree(rich) q0.05** |
| 94 + **월별**(embargo=3) | **2,232** | **86** | **hist_grad_boost(rich) q0.00 · random_forest(rich) q0.05** |

월별 상세(N=20 = 2 feature-set × 10모델): rich hist_grad_boost +0.088 q0.000 · rich RF +0.067

q0.050 · rich 트리 6셀 p≤0.055 전부 양(+) · volume 선형(lda/logistic/ridge)도 **이번엔 +0.04

(p0.07~0.09)** — 표본↑로 관계가 선형으로도 희미하게 검출.

## 3. 해석

- **누수 아님**: embargo(purge) 후에도 생존 → 방법론 규칙("누수는 false positive만")상 진짜.
- **표본취약 아님**: 월별로 횡단면 3배(86)에서도 생존 → 분기 28개 소표본 우연이 아님.
- **비선형 구조 + duty 기여 재확인**: 트리·거리 모델이 선형을 앞섬(비선형), rich(직군 세분)이
volume 단독보다 강함 = 사용자 duty(직무 mix) 가설이 *매출*에서 거듭 지지.

- **효과크기 modest**: 월별 rankIC 0.088 < 분기별 0.119(분기초 달은 부분정보라 per-obs 약함).
월별의 가치는 효과크기가 아니라 **횡단면↑로 유의성 안정화**.

- **유니버스 품질 민감**: 94(clean) 생존, 117(≥10공고, 얇은 종목 포함) 희석 미생존 = 양이 아니라
질. → 확대는 "공고 충분한 종목"으로만.

## 4. 다음 레버

- **월별×깨끗한 KOSPI200**: 채용 공고가 충분한 종목만 골라 유니버스↑(질 유지) + 월별. thin
종목은 배제.

- **피처 심화**: rich가 이김 → 직군 더 세분·OCR 스킬·섹터수요를 rich에 추가.
- **다중소스 융합→매출**: 특허·검색을 같은 매출 타깃에 결합(`fusion_db.py`·`build_patent_revenue_dataset` 존재).
- **경제적 유의성**: rankIC 0.1의 decile-spread·비용후 트레이더빌리티(현 지표에 decile_spread 있음) 정량화.
## 5. 한계

- 정정공시 덮어쓰기(최신본만)·매출 커버리지(82/94 종목). 월별은 분기라벨 재사용이라 표본 3배가
3배 독립정보는 아님(embargo로 누수만 차단, 자기상관은 잔존) — 유의성은 permutation이 보수적으로 반영.

## 부록 — 재현

- 코드(연구, 미커밋; worktree sa-hiring-ml/`research/hiring-ml-phase45`): `app/ml/research/`
{fundamentals_dart, fundamentals_dataset(+signal_freq monthly), duty_categories, magnitude}.py,

bakeoff `--source revenue --revenue-signal-freq --embargo`. 테스트 27 GREEN.

- 스윕: `ML/Hiring/run_revenue94_{purge,monthly}.sh` → `rev94p_fdr.txt`·`rev94m_fdr.txt`.
