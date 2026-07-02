# 대체데이터 ML 실험 보고서 아카이브 (특허 · 채용 · DataLab)

2026-06-23 ~ 07-02 사이 진행한 대체데이터 단독/융합 ML 실험 22건의 보고서를 소스별로 보존한다.
원본은 Notion **experiment DB**에 있으며, 이 폴더는 그 내용을 마크다운으로 **그대로 미러링**한 것이다
(링크가 아니라 본문 포함). 실험 코드 자체는 무거워 main에 머지하지 않았고, 재현용으로 아래 origin
리서치 브랜치에 백업돼 있다. **방향 신호 연구는 종료가 아니라 진행 중**이며, 아래 표는 "지금까지의
관찰"이지 최종 결론이 아니다.

## 소스별 관찰 (현재까지)

| 소스 | 방향 신호 (현재까지) | 지금까지 확정된 가치 | 다음 |
|---|---|---|---|
| **특허** | 단독 방향 신호는 아직 미확인 (8/34종목 횡단면·장기저주파 무신호, 트리/선형 상충=과적합 가능성) | 횡단면 변동성 매그니튜드(단, within-firm 붕괴=정적특성 유의) | 다중소스 융합·피처 확장으로 방향 연구 지속 |
| **채용** | 단독 방향 신호는 아직 미확인 (KOSPI 확장 N=57 BH-FDR 생존 0) | 차기 매출 나우캐스트(94종목 견고, 117 확대서 marginal; rich=직군세분 피처가 매출에 기여) | 매출 나우캐스팅·융합으로 방향 연구 지속 |
| **DataLab** | 단독 방향 신호는 아직 미확인 (전 키워드·유니버스 방향 NULL, PEAD 드리프트는 매출서프라이즈 채널) | 대칭 매그니튜드(미래 변동성·거래량) + 차기 매출 level 나우캐스트(219종목 robust) | 부호 소스 데이터 확보 후 방향 연구 지속 |

**공통 방향(진행 중)**: 지금까지 트레이더블로 확정된 가치는 **매그니튜드/나우캐스트**지만, 주가
*방향* 연구는 계속한다. 방향 융합 3경로(선형·상호작용·PEAD)는 아직 신호를 내지 못했고, 다음 과제는
부호를 공급할 방향소스(뉴스감성·DART LLM 톤 패널·애널리스트 리비전) 데이터 확보와 다중소스 융합이다.

## 목차

### 특허 (`patent/`)
- [BigQuery 적재 + Gemini enrich + 분석기 신호 검증](patent/patent-bigquery-gemini-enrich.md) (06-25)
- [주가 선행성(lead-lag) 작은 검증](patent/patent-price-leadlag.md) (06-25)
- [ML 신호 — 레버1(종목확대 횡단면)·레버4(장기 저주파)](patent/patent-ml-lever1-lever4.md) (06-26)
- [단독 ML — 34종목 횡단면 + 장기 저주파](patent/patent-ml-34stock-crosssection-longhorizon.md) (06-26)
- [단독 ML — 전처리/방법론 결함 교정 후 재검증](patent/patent-ml-preprocessing-fix-revalidation.md) (06-26)
- [ML Stage 2~4 — enrich·이벤트스터디·나우캐스팅·다중소스 융합](patent/patent-ml-stage2-4-eventstudy-fusion.md) (06-26)
- [ML 재검증 — 테스트 보고서](patent/patent-ml-revalidation.md) (06-29)
- [활동 → 차기 실현변동성(매그니튜드) 신호](patent/patent-activity-realized-volatility.md) (06-30)
- [ML 검증 — 딥리서치 + 문헌 교차검증](patent/patent-ml-litreview-verification.md)

### 채용 (`hiring/`)
- [ML — KOSPI 확장 유니버스(57종목) 본격 검정](hiring/hiring-ml-kospi57.md) (06-29)
- [매그니튜드 & 차기 매출 나우캐스팅 검정](hiring/hiring-magnitude-revenue-nowcast.md) (06-30)
- [매그니튜드 & 차기 매출 나우캐스팅 검정 (중복본)](hiring/hiring-magnitude-revenue-nowcast-dup.md) (06-30)
- [매출 나우캐스팅 견고성 검증(월별 신호화 + purge)](hiring/hiring-revenue-nowcast-robustness.md) (07-02)

### DataLab (`datalab/`)
- [단독 ML 모델경연 보고서](datalab/datalab-ml-bakeoff.md) (06-23)
- [검색 → 펀더멘털(차기 분기 매출) 선행성 — 레짐 조건부](datalab/datalab-search-fundamental-revenue-leadlag.md) (06-26)
- [어텐션 → 매그니튜드(변동성·거래량) 선행성 — 견고](datalab/datalab-attention-magnitude-leadlag.md) (06-26)
- [소형/중형 KOSDAQ 어텐션 횡단면 후속 실험](datalab/datalab-kosdaq-smallcap-attention.md) (06-26)
- [검색 가치지형 종합 — 매그니튜드·방향·매출·PEAD](datalab/datalab-search-value-landscape.md) (06-30)
- [ML 테스트 방법론 감사 — 학술 문헌 교차검증](datalab/datalab-methodology-audit.md) (06-30)
- [ML 종합 보고서 — 무엇을 검정했고 무엇이 남았는가](datalab/datalab-ml-comprehensive.md) (07-02)
- [방향성 신호 — 문헌 조사(deep-research)](datalab/datalab-direction-litreview.md) (07-02)

### 공통 / 방법론 (`cross-source/`)
- [대체데이터 ML 방법론 검증 — "제대로 검증했나 / 신호를 놓쳤나"](cross-source/altdata-ml-methodology-verification.md) (07-01)

## 실험 코드 백업 (origin 리서치 브랜치, 미머지)

보고서 본문에서 참조하는 하니스·데이터셋 코드는 아래 브랜치에 있다(트레이더블 신호 확정 전이라 main
미머지, 재현·후속 연구용 보존).

| 소스 | 브랜치 |
|---|---|
| DataLab | `feat/ml-datalab-longhorizon` |
| 특허 | `research/patent-embedding-features`, `research/patent-magnitude-revenue-fusion` |
| 채용 | `research/hiring-ml-phase45` |

## 주의

- 이 문서들은 Notion 원본의 **자동 미러링**이라, 보고서끼리 서로를 가리키는 내부 링크
  (예: `[2026-06-30 실험](...)`)는 이 폴더의 실제 파일명과 다를 수 있다(원본 Notion 기준 링크).
  결론은 위 목차와 각 문서 본문을 따르면 된다.
- 각 보고서의 수치·판정은 **실험 당시 스냅샷**이다. 개별 문서에 "기각/무신호" 같은 단정 표현이
  있어도 그건 그 시점 관찰이며, 방향 연구는 이후에도 계속된다. 최신 판단은 이 README와 프로젝트
  메모리(`altdata-*`)를 우선한다.
