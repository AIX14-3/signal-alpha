# DataLab 방향성 재검정 — PIT 이벤트/수요 키워드 (2026-06-30)

> worktree `sa-ml-longhorizon`. prod 쓰기 0, 데이터 산출물 미커밋(연구). 도구만 브랜치 커밋.
> 한 줄: 직전 "방향 기각"이 **종목명·특허키워드 기반**이었다는 지적에 따라, **설계 의도였던 1차정보(DART 공시) PIT 이벤트 키워드 + 수요·제품 키워드**로 방향을 재검정 → **둘 다 NULL**. 방향 기각이 키워드 방법론 전반으로 확정.

## 배경 / 동기
방향 기각(closeout 2026-06-29)의 근거를 감사한 결과, 실제로 쓰인 키워드는 **(1) 종목명 검색, (2) 종목명+의도접미사, (3) 특허제목 PIT 키워드(빈도 surge)** 뿐이었고, **제품 설계 의도인 "뉴스/DART 1차정보에서 그 시점 추출한 PIT 키워드"는 방향 백테스트에 한 번도 안 쓰였음**(뉴스+LLM 프로덕션 파이프라인은 과거 재현 불가: 네이버뉴스 API 14일 한정, Gemini 미래지식). 즉 방향 기각이 **의도한 방법론에 대해선 미검증 갭**이었음 → 정직하게 재검정.

## 방법 (PIT 정직성 필수)
유니버스 = 소비재 대형주 21종목(KRX 시총상위, `prices_krx250.csv` 일간 종가+거래량 2016~23). 검정 = 기존 방향성 하니스(`cross_sectional_attention.py` 횡단면 IC·16모델 + `bakeoff.py --source period-keyword` PIT 게이팅). **누수방지: rolling-z abn 과거-only, `--pit-features`(full-sample-z 누수 차단), forward 수익만 타깃.** KS11 차감 대신 횡단면 demean.

### 트랙 A — 수요·제품 키워드 (합성지수)
종목당 **3~5개 evergreen 수요키워드**(예: 삼성전자=갤럭시·삼성TV·비스포크, 농심=신라면·짜파게티·너구리…) 79개 일간 수집 → `--composite-search`로 종목별 z평균 = 합성 수요지수. *(키워드 1개는 스파이크 에피소드 과소 → 다중키워드로 다양한 시기 포착.)*

### 트랙 B — DART 공시 PIT 이벤트 키워드
- `collect_dart_disclosures.py`(OpenDartReader.list) → 21종목 2016~23 공시제목 **13,792건** 수집(rcept_dt=PIT 앵커).
- **원시 제목은 검색어로 부적합**(주요주주특정증권등소유상황보고서 등 서식명 → 아무도 검색 안 함). → `gen_dart_event_keywords.py`로 **시장영향 이벤트유형 ~20종**(유상증자·자사주매입·공급계약·합병·소송·횡령·실적·감자·최대주주변경·배당 등) 탐지 → **"[종목명] 이슈" 검색어**(예: "삼성전자 자사주 매입", "카카오 유상증자") 154개, first_avail_date=첫 발생일. 74개 데이터 수집.
- 이벤트유형 목록은 **고정 분류**(체리피킹 0)·발생일 앵커라 PIT-clean.

## 결과 — 둘 다 NULL

| 트랙 | 검정 | 결과 | 판정 |
|---|---|---|---|
| A 수요(합성) | 횡단면 IC 1/2/4주 | +0.000 / −0.001 / +0.016(t1.36) | **NULL** |
| A 수요(합성) | 16모델 1개월 best rankIC | +0.034, **Dbase 전부 ≤0** | **NULL** |
| B DART이벤트(합성) | 횡단면 IC 1/2/4주 | +0.006 / +0.008 / +0.002 (전부 t<0.6) | **NULL** |
| B DART이벤트(합성) | 16모델 1개월 best rankIC | +0.034, **Dbase 전부 ≤0** | **NULL** |
| B DART이벤트(PIT게이팅) | period_keyword bakeoff best rankIC | **+0.000** (대부분 음수) | **NULL** |

- 어떤 키워드 구성(종목명·수요·PIT 이벤트)도, 어떤 모델·호라이즌도 **다수 베이스라인을 못 이김.** 횡단면 IC·rankIC 전부 ≈0.
- 수요 다중키워드(다양한 시기 포착)·DART 실제사건 앵커(호재/악재 포함)로 보강해도 방향 신호 부재.

## 소스 처리
- **빅카인즈**: 유료 전환 확인 → 이번 트랙서 제외.
- **웹서치**: 백테스트엔 후견편향(non-PIT)이라 미채택. 라이브/탐색용으로만 유효.

## 결론
- **DataLab 검색 → 주가 방향 = 기각 확정.** 이제 종목명·의도·특허·**수요·DART이벤트(설계 의도 방법론)** 전 키워드 방법론에서 NULL → 직전 기각의 미검증 갭 해소.
- 검색의 유일한 가치는 **매그니튜드(미래 변동성/거래량)** — 별도 확정(반석)·제품화(attention_spike). 방향 재시도 금지.

## 재사용 산출물 (도구만 브랜치 커밋, 데이터 미커밋)
- `app/ml/keywords/sources.py::DartTitleSource`, `extract.py`(extra_stopwords)
- `scripts/collect_dart_disclosures.py`(공시제목 수집), `gen_dart_event_keywords.py`("[종목명] 이슈" 변환), `extract_dart_keywords.py`(원시 surge, 참고), `_gen_demand_keywords.py`(수요 큐레이션)
- 데이터(미커밋): `dart_disclosures.json`, `demand_daily.csv`, `dart_event_daily.csv`, `kw_demand/`, `kw_dart_event/`, `dart_event_meta/`

관련: [[attention-lead-lag-evidence]] · [[ml-bakeoff-datalab-result]] · `2026-06-29-datalab-direction-fundamental-closeout.md` · `2026-06-30-datalab-magnitude-model-selection.md`
