# DART 규칙 정하기 & 네이버 데이터랩 전처리 — 팀원 가이드

> 대상: 분석기/전처리 코드를 만지는 팀원.
> 한 줄 요약: **전처리는 결정론(규칙·임베딩·통계지표)으로 한다. 생성형 LLM은 끝단 종합(설명)에만 쓴다.**

## 왜 결정론인가 (먼저 읽어주세요)

프로젝트 핵심 원칙은 **"판정(점수·방향·발행)은 규칙·ML·게이트가 정하고, LLM은 설명만 한다"**
([worker-redesign.md](worker-redesign.md)). 전처리 결과는 게이트2 → ML/DL → 메타러너로 흘러
**판정에 영향**을 준다. 그래서 전처리에 생성형 LLM(매번 답이 달라짐)을 쓰면:

- 같은 공시·같은 검색트렌드인데 결과가 흔들림 → **백테스트·메타러너 학습 불가**
- 비용·지연 증가, "LLM은 설명만" 원칙 위배

→ 전처리는 **같은 입력이면 항상 같은 출력**이어야 한다. 임베딩 모델(BGE-M3)은 고정 가중치라
결정론이므로 OK. 규칙(정규식·임계)·통계지표도 OK. **생성형 LLM은 금지.**

---

# Part 1. DART 공시 — "규칙성" 정하기

공시는 자연어 텍스트다. 여기서 두 종류의 결정론 정보를 뽑는다:
**(1) 규칙추출**(명시적 사실: 이벤트 유형·재무수치)과 **(2) 임베딩**(의미 벡터).

## 1-1. 보고서 분류 규칙 — `analyzers/dart/rules.py`

`classify_dart_report(report_name)` 가 **보고서 이름 문자열**만 보고 결정론적으로 분류한다.
현재 규칙(위에서부터 먼저 매칭되는 것 적용):

| 조건(보고서명에 포함/플래그) | event_type | 방향 | 임팩트 | needs_review |
|---|---|---|---|---|
| 정정/correction/amendment | `correction` | neutral | low | ✅ |
| "주요사항보고서" | `material_event` | mixed | high | |
| "임원" + "주요주주" | `insider_ownership` | neutral | low | |
| "사업/반기/분기보고서" | `periodic_report` | neutral | medium | |
| "기업지배구조보고서" | `governance_report` | neutral | medium | |
| (그 외 전부) | `dart_disclosure` | unknown | low | ✅ |

**규칙을 추가/수정하는 법**
1. `classify_dart_report` 에 `if _contains_any(name, ("새 보고서명",)): return DartClassification(...)`
   분기를 **우선순위 위치**에 추가한다(먼저 매칭되는 게 이긴다).
2. 새 `event_type` 문자열은 `analyzers/dart/llm.py` 의 `_HIGH_IMPACT_EVENT_TYPES` 등
   다른 곳에서 참조될 수 있으니 검색해 정합을 맞춘다.
3. **테스트 추가**: `tests/test_dart_*`에 "이 보고서명 → 이 분류" 케이스를 박아 회귀를 막는다.
4. 절대 금지: 분류를 LLM에 맡기기, 종목 코드/티커 하드코딩.

> ⚠️ 함정: `impact_level`은 needs_review/우선순위에 쓰인다. 새 유형의 임팩트를 정할 때
> "고임팩트면 medium/high"로 일관되게. 애매하면 `needs_review=True`로 보수적으로.

## 1-2. 재무수치 규칙추출 — `analyzers/dart/financials.py`

`extract_dart_financial_metrics(text)` 가 본문에서 **정규식**으로 매출/영업이익/순이익 숫자를
뽑고 단위(조원/억원/백만원)를 KRW(백만원 기준)로 환산한다. 임베딩은 "이 공시가 매출 얘기다"
까지는 알아도 **"영업이익 1,234억원"이라는 정확한 숫자는 못 뽑는다** → 규칙추출이 보완.

**새 지표 추가**: `_METRIC_PATTERNS`에 `(이름, re.compile(r"키워드\D{0,20}([-+]?\d[\d,]*...)..."))`
패턴을, 새 단위는 `_UNIT_TO_KRW_MILLION`에 추가. 한국어/영문 키워드 변형을 `(?:매출액|매출|revenue)`
처럼 묶는다.

## 1-3. 본문 임베딩 — `EMBED_DART` → `dart_chunks` / `dart_document_features`

공시 본문은 BGE-M3(1024d)로 임베딩해 `dart_chunks`에 적재한다(`DartEmbedTaskHandler`).
**1024차원 raw를 모델에 직접 넣지 않는다**(표본 대비 차원 과다 = 과적합). 대신 소수의
**스칼라 피처**로 환원한다 — 지금은 신규성(`mean_prior_distance`: 새 공시 ↔ 같은 종목 과거
공시 평균 코사인거리)을 `dart_document_features`에 적재(`embedding_features.mean_vector`,
021 마이그레이션).

- 본문이 없고 제목만 있는 공시는 임베딩을 **건너뛴다**(가치 낮음·BGE-M3 낭비).
- 피처를 늘릴 땐 "스칼라/소수·결정론" 원칙 유지(예: 리스크 센트로이드 유사도). raw 벡터 투입 금지.

---

# Part 2. 네이버 데이터랩 — 전처리 과정

## 2-1. 데이터 성격부터: 텍스트가 아니라 "검색량 시계열"

데이터랩은 키워드별·일자별 **검색 지수(숫자)** 다. 텍스트가 아니므로 **임베딩 대상이 아니다.**
통계·규칙으로 피처화하는 게 맞다.

## 2-2. "23,000행 = 과적합" 오해 풀기 (중요)

삼성·하이닉스 키워드를 모으면 원본이 수만 행이지만, **이건 모델 입력이 아니다.**
`analyzers/datalab/rules.py:evaluate_indicators` 가 이미 **(종목, 날짜)당 점수 `score ∈ [-1,1]`
하나**로 집계한다. 모델이 보는 건 종목·일자당 소수의 신호다. **이 집계 자체가 과적합 방지.**
키워드별로 피처를 펼치지 말 것(차원 폭발 → 과적합).

## 2-3. 어떤 지표를 뽑나 — `analyzers/datalab/indicators.py` (`compute_indicators`)

원본 검색행 → 결정론 수치 피처로 집계 (clock-free, `as_of`는 로더가 줌):

- **모멘텀**(`momentum_pct`): 최근 평균 vs 이전 평균 (관심 증가/감소)
- **스파이크**(`spike_ratio`): 급등 관측 비중
- **리스크 모멘텀**(`risk_momentum_pct`): 리콜·불매·논란 등 리스크 키워드 검색 상승(→ 약세)
- **변화율**(`avg_change_pct`): 행별 변화율 평균
- 카테고리 가중 평균(`datalab_category_stocks.weight`)으로 여러 테마를 종목에 합산

## 2-4. 점수화 — `evaluate_indicators`

지표들을 컴포넌트로 환산해 합치고 `[-1, 1]`로 클램프:

```
score = clamp(momentum + spike + change + risk, -1, 1)
```

가드/예외:
- 관측 0건 → `direction="unknown"`, `no_data`
- 관측 < `min_observations` → `insufficient_history`
- 최근 관측이 `stale_days` 초과 → `stale_data`
- **표본 가드**: 이전 구간 관측 < `min_prior_observations` → 비율 기반(모멘텀·변화율) **억제**(0),
  스파이크(절대 비중)는 유지 → `low_base`
- 임계·가중·스케일은 전부 `DataLabRuleConfig`(env)에서 온다. **하드코딩 금지.**

> ⚠️ 알려진 이슈(L2): `momentum`과 `change`가 둘 다 "상승"을 측정해 **이중 계산**된다.
> 의도적으로 현재 보류 중이며, `tests/analyzers/test_datalab_doublecount.py` 가 동작을 핀으로
> 박아뒀다. 이 식을 바꾸려면 그 테스트도 함께 갱신할 것(점수 설계 결정은 유지보수자와 합의).

## 2-5. 검색-가격 타이밍(lead-lag) — `agents/datalab/lead_lag.py`

검색 급증이 주가보다 **앞섰나(catalyst)/뒤따랐나(fomo)/단순추종(price_led)**를 OHLCV와 비교해
결정론적으로 라벨링한다. 이건 **추적용 태그**이고 매수/매도 신호가 아니다. (과거에는 이 라벨을
LLM이 재서술했지만 그 LLM 단계는 제거됨 — 결정론 라벨만 쓴다.)

## 2-6. 새 키워드/카테고리·임계 튜닝

- 키워드/카테고리 매핑: `datalab_category_stocks` 등 DB·시드로 관리(코드 하드코딩 X).
- 임계/가중: `DataLabRuleConfig` env로 조정 → 재현성·실험 용이.
- 바꾸면 `tests/analyzers/test_datalab_*` 회귀 테스트로 고정.

---

# 공통 체크리스트 (전처리 PR 셀프리뷰)

- [ ] 생성형 LLM을 판정 경로에 넣지 않았다(끝단 종합만 LLM).
- [ ] 같은 입력 → 같은 출력(결정론). 난수/시계/외부호출 비결정 요소 없음.
- [ ] 종목·티커·임계·경로 **하드코딩 없음**(DB/`is_target`/env에서).
- [ ] 피처는 **소수·스칼라**(임베딩 raw·키워드 펼치기 금지 = 과적합 방지).
- [ ] 동작을 박는 테스트 추가/갱신.

## 관련 파일
- DART: `analyzers/dart/rules.py`, `financials.py`, `embedding_features.py`,
  `orchestrator/dart/tasks.py`(EMBED_DART), 마이그레이션 `021_dart_chunks`·`022_dart_document_features`
- DataLab: `analyzers/datalab/indicators.py`, `rules.py`, `normalize_rules.py`,
  `agents/datalab/lead_lag.py`
- 설계/원칙: [worker-redesign.md](worker-redesign.md), [architecture.mermaid](architecture.mermaid),
  [meta-learner-training.md](meta-learner-training.md)(과적합 가드)
