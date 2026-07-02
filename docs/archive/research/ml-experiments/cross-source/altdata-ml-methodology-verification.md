# 대체데이터 ML 방법론 검증 — "제대로 검증했나 / 신호를 놓쳤나" (2026-07-01)

> 채용/검색/특허 대체데이터로 KOSPI를 검정해 온 우리 ML 방법론을, 최신 공식문서·동료심사
> 논문·신뢰 GitHub(URL 포함)으로 교차검증했다. 특히 "검색→방향", "검색→매그니튜드→방향"
> 체인이 제대로 검증됐는지, 방법론 결함으로 *신호를 놓쳤을* 가능성을 본다.
> 
> **한 줄 결론: 방법론상 신호를 놓친 정황은 없다. (i) 데이터누수 미차단은 *거짓 양성*만
> 만들지 *신호 놓침*(false negative)을 만들지 않으므로 우리 방향·매그니튜드 무신호는 안전.
> (ii) meta-labeling은 *기존 방향엣지를 정제*할 뿐 새 방향을 발굴하지 않아, 방향엣지 0인 우리에겐
> 무용. (iii) 검색 체인(검색→방향 기각·검색→매그니튜드 견고)은 B 세션이 이미 전수 검증·문헌
> 부합. 유일한 미검증 조건은 채용→방향 @ *연간 호라이즌·소형주*(Belo 2014).**

---

## 1. meta-labeling — 방향엣지 없으면 무용 (막다른 길)

메타라벨링 = 1차 모델이 **방향(side)**, 2차 ML이 **베팅여부·크기(size)** 를 결정하는 2단계 기법.

- 정의(AFML Ch.3): "secondary ML model that learns how to use a primary exogenous model."
🔗 https://en.wikipedia.org/wiki/Meta-Labeling

- **효능(HIGH)**: 기존 방향모델의 **정밀도/F1·베팅사이즈·오탐필터를 개선**할 뿐 — "the role of
the secondary ML algorithm is to determine whether a positive from the primary model is true or

false." 동료심사 프레임워크: Joubert, *J. Financial Data Science* 2022.

🔗 https://jfds.pm-research.com/content/4/3/31 · 코드 🔗 https://github.com/hudson-and-thames/meta-labeling

- **함의**: 1차 모델에 방향엣지가 없으면 필터할 게 없다. 우리는 방향엣지 0 → **지금은 무용.**
- 사용자 아이디어 "매그니튜드→방향"은 메타라벨링이 **아님**(메타라벨링은 side가 *먼저*). 매그니튜드
(변동성)는 부호가 없어 방향을 만들 수 없다 → **막다른 길.**

## 2. purged/embargoed CV — 우리 무신호는 false negative 아님

겹치는 라벨(예: h일 선도수익률을 k<h 간격 샘플)에서 표준 CV는 train↔test 누수로 성능을 **과대평가**.

- sklearn 공식: "classical cross-validation techniques such as KFold... would result in unreasonable
correlation between training and testing instances" on time series.

🔗 https://scikit-learn.org/stable/modules/cross_validation.html

- purge=테스트 라벨기간과 겹치는 학습표본 삭제 / embargo=테스트 직후 표본 추가 삭제 (AFML Ch.7).
🔗 https://www.risklab.ai/research/financial-modeling/cross_validation

🔗 https://en.wikipedia.org/wiki/Purged_cross-validation

- **방향(HIGH)**: 누수는 **거짓 고성능**을 만든다 — "falsely high-performance scores and models that
fail in live trading." purge는 엣지를 *제거*만 하지 *생성* 안 함 → **비대칭: 미차단은 false

positive 위험, false negative 아님.**

- **함의(핵심)**: 방향·매그니튜드는 (누수로 부풀 수 있는) 성적에서도 무신호 → purge하면 더 무신호.
**숨은 신호를 놓친 게 아니다.** 단 유일 양성인 **채용→매출(94)은 purge+embargo 재검정 권장**

(다만 분기말 as_of·분기 라벨은 비중첩이라 누수 위험 낮음).

## 3. 검색 체인 — 이미 전수 검증됨(B 세션) + 문헌 부합

`_/devlog/workunits/B-datalab-attention.md` (2026-06-26):

| 체인 | 우리 결과(B) | 문헌 |
| --- | --- | --- |
| 검색→방향 | ❌ 기각(look-ahead 적발 rankIC 0.185→PIT 0.037; 대형·소형 KOSDAQ 무) | ✅ attention→방향 약함/음/무의미 🔗 https://eaesp.fgv.br/sites/default/files/legacy/pesquisa-eaesp-files/arquivos/investor_attention.pdf |
| 검색→**매그니튜드** | ✅ **견고**(미래 거래량 IC +0.37 t41, 전 era·양시장) | ✅ attention→변동성/거래량 강함 🔗 https://link.springer.com/article/10.1186/s40854-023-00606-y |
| 검색→매그니튜드→방향 | 안 함(정당) | 매그니튜드 부호無 + 검색→방향 무 → 불가능 |

→ **"검색→방향, 검색→매그니튜드→방향" 검증은 제대로 됐다.** 검색→매그니튜드에서 멈추고 제품

플래그(attention_spike, z>3.5→거래량 3.1×)로 간 것이 문헌상 정답.

## 4. 유일한 미검증 조건 — 채용→방향 @ 연간·소형주

> Belo, Bazdresch, Lin (2014), *Journal of Political Economy*: 채용률 +10%p → 다음 **1년** 주가
> 수익률 **−1.5%p**; 효과는 **소형주 집중, 대형주엔 약함**. 🔗 https://www.journals.uchicago.edu/doi/10.1086/674549 (검증 3-0)

우리 채용→방향은 **5~60거래일·대형주(KOSPI)** 만 검정 → 무신호가 문헌과 오히려 일관(신호 없는

조건만 봄). **연간(h≈252)·소형/KOSDAQ 조건은 아무도 안 봤다.**

## 5. 결론 / 다음 우선순위

- **"제대로 검증했나?"** 대체로 예 — 검색 체인 완결, 방향·매그니튜드 무신호는 누수 false
negative 아님.

- **"신호를 놓쳤나?"** 방법론(누수)·기법(meta-labeling) 때문은 아님. 단 **조건(호라이즌·유니버스)**
을 안 본 게 하나(채용 연간·소형주).

- **다음 레버(문헌 근거순)**:
1. ⭐ 채용→방향 @ 연간(h≈252)·소형/KOSDAQ — Belo(2014) 유일 미검증 조건.
1. 채용→매출(94) purge+embargo 재검정 — 유일 양성 견고성 확인.
1. ~~meta-labeling~~(방향엣지 없어 무용) · ~~검색→변동성~~(B 완료).
## 부록 — 검증 방법

- 딥리서치(5각도 웹검색→수집→3표 적대적검증→종합) + 타깃 검증 에이전트 2건(meta-labeling·
purged CV). 사설 블로그 배제, 공식문서·동료심사·신뢰 GitHub만 인용. 일부 표는 계정 세션

한도로 중단 후 타깃 재검증으로 보완. mlfinlab.com은 현재 스팸 하이재킹 상태라 readthedocs

미러·RiskLab AI·Hudson&Thames·Wikipedia(AFML 인용) 사용.

- 관련 실험: [2026-06-30 채용 매그니튜드·매출](2026-06-30-hiring-magnitude-revenue.md).
