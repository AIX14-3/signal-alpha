# Price Collector (C-4)

키움증권 OpenAPI+ 기반 **주가 · 수급 데이터 수집기**. MVP 종목의 일봉 OHLCV와
투자자별 순매수, 기본 정보를 수집해 공유 PostgreSQL의 `ohlcv_data` 테이블에
적재한다. (이슈 [C-4])

## 책임 범위

- 수집 대상 TR
  - `OPT10081` 주식 일봉 차트 → OHLCV
  - `OPT10059` 종목별 투자자 매매동향 → 외국인/기관 순매수
  - `OPT10001` 주식 기본 정보 → 시가총액 등 스냅샷
- 적재 테이블: `ohlcv_data` (실행 추적은 `collector_runs`, `collector_type='PRICE'`)
- 수집기는 **LLM을 호출하지 않으며**, 방향/점수/요약 등 분석 필드를 만들지 않는다.
  정규화된 수치만 저장한다.

## 실행 환경 (중요)

키움 OpenAPI+는 **Windows COM(DLL) 전용**이라 리눅스(AWS EC2)에서 직접 실행할 수
없다. 따라서 이 서비스는 **별도 Windows 머신**에서 실행해 PostgreSQL에 적재하고,
`agent-worker` 등 분석 측은 DB 조회만 수행한다. (스펙 제약 1)

> **이 수집기는 HTTP 엔드포인트가 없다.** FastAPI 서비스가 아니라 **배치 CLI**이며,
> 호출 대상 주소가 아니라 작업 스케줄러/수동으로 실행하는 프로그램이다. 결과는
> 응답이 아니라 PostgreSQL에 적재되고, 분석 측은 그 DB를 읽는다.

```text
┌──────────────────────── Windows 머신 ────────────────────────┐
│  키움 OpenAPI+ (COM/DLL, 로그인 세션 필요)                    │
│        │  pykiwoom block_request (OPT10081/10059/10001,       │
│        │                            OPT20006/20004)           │
│        ▼                                                      │
│  price-collector (배치 CLI · 엔드포인트 없음)                 │
│     python -m app.main          ← 종목 주가/수급             │
│     python -m app.sector_main   ← 업종 지수                  │
│     · Windows 작업 스케줄러로 장 마감 후 자동 실행            │
└────────┬─────────────────────────────────────────────────────┘
         │  psycopg (DATABASE_URL) · 쓰기 전용
         ▼
┌──────────────────────── EC2 / 관리형 ────────────────────────┐
│  PostgreSQL : ohlcv_data · sector_ohlcv · collector_runs     │
│        ▲                                                      │
│        │  DB 조회만 (수집기를 직접 호출하지 않음)             │
│  agent-worker(:8011) · main-server(:8000) · web(:3000)       │
└──────────────────────────────────────────────────────────────┘
```

- TR 요청 제한: 초당 5회 / 분당 100회 → `RateLimiter`가 기본 0.2초 간격 + 분당
  상한을 강제한다. (제약 2)
- 차트 조회는 `수정주가구분=1`로 무상증자·액면분할을 반영한다. (제약 4)
- 실시간 값은 평일 09:00~15:30에만 유효하므로 배치는 장 마감 후 실행을 권장한다.

## 설치 & 실행

```bash
# 코어 의존성 (psycopg)
pip install -e .

# Windows 수집 머신에서 키움 연동 의존성 추가
pip install -e ".[kiwoom]"

# 키움 로그인 세션이 있는 상태에서 실행
python -m app.main --all                                  # MVP 전체
python -m app.main --tickers 005930 000660 --base-date 20260608
```

환경 변수는 레포 루트 `.env.example`을 따른다. 핵심은 `DATABASE_URL`이며,
선택값으로 `KIWOOM_TR_DELAY_SEC`, `KIWOOM_TR_MAX_PER_MINUTE`,
`PRICE_LOOKBACK_DAYS`, `PRICE_USE_ADJUSTED`가 있다.

## 테스트

키움/DB 없이 fake로 파싱·병합·파이프라인을 검증한다.

```bash
python -m unittest discover -s tests
```

## 구조

```
app/
  core/        설정 + TR 코드/ MVP 종목·업종 코드 상수
  kiwoom/      KiwoomClient 추상화 + pykiwoom 구현 + 값 파싱
  collectors/  TR별 수집기 (일봉 / 투자자 / 기본정보)
  schemas/     정규화 dataclass + ohlcv_data 행 병합
  storage/     OhlcvRepository (PostgreSQL upsert + collector_runs)
  pipeline.py  종목 루프 + 실행 추적 오케스트레이션
  main.py      배치 CLI 엔트리포인트
```

## 미적재 데이터

매출/영업이익 등 재무 원본은 키움이 제공하지 않으므로 DART 수집기(별도 이슈)에서
보완한다. (스펙 4) 업종 차트(`OPT20004/20006`) 상수는 정의해 두었으며 업종 상대강도
지표용 수집은 후속 작업으로 둔다.
