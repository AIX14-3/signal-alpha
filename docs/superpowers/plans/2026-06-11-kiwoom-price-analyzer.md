# 키움 주가 분석기 (PRICE Analyzer) 구현 계획

작성일: 2026-06-11 · 브랜치: `feat/kiwoom-api-analyzer`

## 0. 설계 결정 — DART 분석기와 같은 서비스, 별도 패키지

분석기는 **`services/agent-worker/app/analyzers/price/`** 로 만든다. DART 분석기(`analyzers/dart/`)와 **같은 서비스 안의 형제 패키지**이며, 별도 서비스로 분리하지 않는다.

근거:

1. **오케스트레이터 구조** — `AgentOrchestrator`는 source별 `SourcePipeline`(collector + analyzer)을 돌려 `dict[SourceType, SourceResult]`로 합산한다 (`orchestrator/pipeline.py`). 주가 분석도 결국 하나의 source일 뿐이므로 같은 합산 흐름에 들어가야 final_signals 계산에 합류할 수 있다. 별도 서비스로 빼면 `SourceResult` 계약 공유와 결과 합산이 깨지고 배포 인프라만 늘어난다.
2. **`dart/` 안에 넣지 않는 이유** — DART 규칙은 공시 제목 텍스트 분류(`dart/rules.py`)이고, 주가 분석은 OHLCV/수급 수치 시계열 분석이다. 입력·로직·테스트 픽스처가 전혀 달라서 패키지를 분리해야 한다.
3. **수집기와의 경계 (중요)** — "키움 API 분석기"라는 이름이지만, **분석기는 키움 API를 직접 호출하지 않는다.** `Analyzer` 프로토콜 docstring("do not call external source APIs")과 `docs/architecture.md`의 원칙대로, 키움 호출은 `services/price-collector`(REST 실시간 폴링 수집기)가 담당해 PostgreSQL `ohlcv_data` / `price_snapshots`에 적재하고, 분석기는 **DB만 읽는다.** REST 수집기는 `feat/kiwoom-rest-realtime-collector`에서 구현되었으며, 분석기는 DB에 행이 있기만 하면 된다.

소스 타입 이름은 벤더명(KIWOOM)이 아니라 데이터 도메인 기준으로 **`PRICE`** 를 권장한다 (수집기를 나중에 한국투자증권 등으로 바꿔도 분석기는 무관해짐).

## 1. Phase 1 — 데이터 계약

- `app/schemas/evidence.py`: `SourceType`에 `"PRICE"` 추가.
- 수치 데이터 운반: `RawEvidence.metadata`(dict)에 OHLCV/수급 행 리스트를 담는다. 행당 키: `trade_date, open, high, low, close, volume, foreign_net, institution_net, change_pct, market_cap` (= `ohlcv_data` 컬럼과 1:1).
- `app/collectors/price/ohlcv_reader.py`: `Collector` 프로토콜 구현. 외부 API 호출 없이 `packages/data-access` 헬퍼로 `ohlcv_data`(최근 N영업일, 기본 120일)와 `sector_ohlcv`(업종 상대강도용)를 조회해 `RawEvidence`로 변환. 데이터가 비었거나 최신일이 오래되면 `metadata["stale"]` 표시.

## 2. Phase 2 — 분석기 룰 엔진

`analyzers/price/` 3개 모듈, DART의 순수함수 스타일(`dart/rules.py`)을 따른다:

- `indicators.py` — 순수 계산 함수만:
  - 이동평균 5/20/60 및 정배열/역배열, 골든/데드크로스
  - RSI(14), 최근 20일 대비 거래량 z-score
  - 외국인/기관 순매수 연속일수, 누적 순매수 방향
  - 변동성(20일 표준편차), 갭 발생 여부
  - 업종 대비 상대강도 (`sector_ohlcv` 있을 때만)
- `rules.py` — 지표 → `direction`/`score`/`risk_flags` 매핑 룰 (LLM 없음, 결정적):
  - score는 **[-1.0, +1.0]** 부호 있는 강도로 정의 (direction과 부호 일치). 0~100 변환은 집계(D-1) 단계 책임.
  - risk_flags 예: `"low_liquidity"`, `"high_volatility"`, `"stale_data"`, `"insufficient_history"`
- `analyzer.py` — `Analyzer` 프로토콜 구현체 `PriceAnalyzer`. evidence가 비면 `data_status="failed"`, 일부 결측이면 `"partial"` 반환.

## 3. Phase 3 — 오케스트레이터 / API 연결

- `SourcePipeline(source="PRICE", collector=OhlcvReader, analyzer=PriceAnalyzer)` 등록.
- `api/routes/price.py` 추가 (`api/routes/dart.py` 패턴): 단일 종목 분석 트리거/조회 엔드포인트.
- `orchestrator/persistence.py` 경유로 `agent_results`에 저장 (기존 저장 경로 재사용).

## 4. Phase 4 — 테스트

- `tests/analyzers/price/test_indicators.py`: 고정 캔들 픽스처로 MA/RSI/z-score 경계값 검증.
- `tests/analyzers/price/test_rules.py`: 지표 조합 → direction/score/risk_flags 표 기반 검증.
- `tests/collectors/price/test_ohlcv_reader.py`: fake repo로 DB 의존 없이 검증 (price-collector의 `tests/fakes.py` 패턴 참고).
- 실행: `cd services/agent-worker && uv run pytest`

## 5. 범위 밖 (후속 브랜치)

분석기 완성 후 **별도 브랜치**에서 임시 예측 파이프라인을 만들어, 신뢰도·예측 점수가 실제 주가와 얼마나 일치하는지 검증한다 (`backtest_results` / `score_history` 테이블 활용 예정). 이 하네스 엔지니어링(스킬·훅 루프 반복 개선)은 본 계획에 포함하지 않는다.

## 선행 조건

1. REST 실시간 수집기(`feat/kiwoom-rest-realtime-collector`)가 머지되어 `ohlcv_data`에 실데이터가 적재될 것 (분석기 개발 자체는 픽스처로 가능하므로 병행 가능). 단, 실시간 수집만으로는 과거 이력이 없으므로 **120일 백필(후속 작업) 전까지 분석기는 `insufficient_history`가 정상**이다.
2. score 규약([-1,1])에 대한 팀 합의 — D-1 집계 가중치(dart 0.35 / report 0.40 / alt 0.25)에 PRICE를 어떤 가중치로 넣을지는 별도 결정 필요.
