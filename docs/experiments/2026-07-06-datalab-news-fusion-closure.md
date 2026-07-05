# DataLab 뉴스감성 융합 라인 — 공식 종료 (2026-07-06)

**한 줄 결론:** 검색×뉴스감성(및 검색×DART 부호) **방향 융합 라인을 공식 종료**한다. 밀집 실데이터 4방법이 sweep-wide 검정에서 전멸했고(감성은 coincident, not leading), 유일한 잔여 레버였던 BigKinds는 **유료 전환·웹서치=non-PIT**로 값싼 무료 데이터가 아님이 확인됐다. 재개는 사용자 게이트(PIT 뉴스 아카이브 확보) 통과 시에만.

## 1. 배경 — 왜 종료하나

DataLab(네이버 검색) 방향성 알파는 단일소스 전 키워드에서 기각됐고(look-ahead/기간국소 아티팩트), 문헌조사(`2026-07-02-datalab-direction-literature-research.md`)는 남은 유일 경로를 **융합**으로 지목했다: *부호는 뉴스/공시 감성, 크기·타이밍은 검색.* 이 문서는 그 융합 경로가 데이터 크럭스까지 실데이터로 검정된 결과 **strong true-null**임을 확정하고 라인을 닫는다.

## 2. 확정된 null 증거

| 검정 | 결과 | 근거 문서 |
|---|---|---|
| 검색×DART 공시 부호 융합 | **기각** — 어텐션이 signed 반응을 증폭이 아니라 **소멸**(hi−lo −0.22%, perm_p 0.92). Ranco 구조가 이산 공시엔 반대 | `2026-07-02-datalab-dart-sign-fusion.md` |
| 부호 키워드 쌍(호재/악재) | **기각 + 데이터 기근** — 60쿼리 중 26 무데이터, 양측 보유는 9개 초대형주뿐 | `2026-07-02-datalab-signed-keyword-pairs.md` |
| 조건부 반전(어텐션 게이트) | **무신호** — 유의 반전은 유동성 대형주(리테일 메커니즘과 반대), 0/42 셀 BH-FDR 생존 | `2026-07-01-datalab-conditional-reversal.md` |
| 뉴스감성×검색 4방법(밀집2026 횡단면; 뉴스/검색/concat/tone×attention Ranco 게이트) | **전부 BH-FDR 생존0** — per-period t<1.5, forward IC≈0. 측정 타당도는 견고(동시점 IC0.22·perm_p0.002) → **감성은 coincident, not leading.** h=1 검정력 충분(MDE0.025<0.05) | 07-03 파일럿(코드 소실, 아래 §3) |

핵심 메커니즘 요약: 검색 어텐션은 **"이미 반영됨"의 표식**이지 방향 게이트가 아니다. 대형주 공시·뉴스는 d+1까지 효율적으로 가격에 반영되어, 감성 부호를 얹어도 forward 방향 엣지가 생기지 않는다.

## 3. 소실된 07-03 파일럿 — 결론만 보존

2026-07-03 뉴스감성×검색 융합 파일럿(도구 `news_sentiment.py`·`news_fusion_pilot.py`·`news_diagnostics.py`·`backfill_news_sentiment.py` + 테스트 18, 총 49 GREEN)은 **제거된 `sa-ml-longhorizon` 워크트리의 미커밋본**이라 코드가 소실됐다(스태시·브랜치·잔존 dir 없음). 그러나 **결론(strong true-null)은 위 표 마지막 행 + 메모리 [[news-sentiment-fusion-pilot]] + 커밋된 `2026-07-02-datalab-dart-sign-fusion.md`에 보존**되어 재현/의사결정에 지장 없음. 진단(`interpret_null`)의 판정: 측정은 유효하나 신호가 leading이 아님 = **base-case true-null**(놓친 신호 아님).

## 4. BigKinds 결정 (④) — 종료 처리

핸드오프가 남긴 "BigKinds 무료 ₩0 export" 전제는 **커밋된 기록과 상충**한다:

> `datalab-search-value-landscape.md`(main): *"빅카인즈 유료 전환으로 제외, 웹서치는 후견편향(non-PIT)으로 미채택."*

- BigKinds 전문/아카이브 접근은 **유료 전환**됨(무료가 아님). 무료 웹 export는 본문 절단·아카이브 제한이 있고, 일반 웹서치는 **non-PIT(후견편향)**이라 백테스트 부적격.
- 부호소스(PIT 뉴스감성 패널)가 없으면 다중소스 융합(③)은 검정 자체가 불가 — 즉 **③과 ④는 같은 데이터 확보 문제**다.

**결정:** 뉴스융합 라인 종료. BigKinds/PIT 뉴스 아카이브 확보는 **사용자 게이트**로 남긴다(비용·PIT 적격성 재검증이 선행돼야 재개). 재개 시 기존 융합 하니스(`app/ml/bakeoff_ab.py` Method B, `scripts/pead_nowcast_fusion.py`)가 그대로 부호소스를 소비할 수 있으므로 코드 재작업은 불필요.

## 5. 확정 가치지형 (변동 없음)

DataLab 검색의 트레이더블 가치 = **매그니튜드(미래 변동성/거래량, 제품화 완료) + 차기분기 매출 level 나우캐스트(219종목 robust) + 시장수준 FEARS 리스크오프(첫 방향성 생존, 트레이더빌리티 marginal).** **종목별 트레이더블 방향 알파는 없음.**

## 6. 후속 (이 종료와 별개)

- 방향 탐색은 ad-hoc 대신 **사전등록 자동 스윕**(`app/ml/sweep.py`·`sweep_grid.py`)으로 재개 — sweep-wide BH-FDR + held-out 확인으로 정직하게 전수·감사. 뉴스융합 셀은 부호소스 GATE로 표시되어, PIT 뉴스 확보 전엔 자동 스킵된다.
- 메인 문서 `datalab-search-value-landscape.md`(main)의 verdict는 이미 "방향=전키워드 NULL"을 명시 — 별도 PR 시 이 종료(융합-방향 라인)를 한 줄 반영 권장.

## 참조
- 실험: `2026-07-02-datalab-{dart-sign-fusion,signed-keyword-pairs,direction-literature-research,ml-comprehensive-report}.md`, `2026-07-01-datalab-conditional-reversal.md`
- 메모리: [[news-sentiment-fusion-pilot]] · [[altdata-direction-signal-wall]] · [[datalab-revenue-nowcast-pead]]
