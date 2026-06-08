# Architecture

Signal Alpha uses a monorepo with independently deployable services.

```text
frontend web
  -> main-server
    -> agent-worker
      -> collectors
      -> analyzers
      -> orchestrator
```

The main server remains the user-facing API boundary. The agent worker handles source collection and LLM/RAG analysis.

## 데이터 수집기 런타임 (Price / Sector Collector)

키움 OpenAPI+ 기반 수집기(`services/price-collector`)는 **위 서비스들과 실행 환경·호출 방식이 다릅니다.** FastAPI 서비스가 아니라 **배치(batch) 프로그램**이며, **호출하는 HTTP 엔드포인트가 없습니다.** 키움 OpenAPI+가 Windows COM(DLL) 전용이라 EC2(Linux)·Docker에서 실행할 수 없기 때문에, 별도 Windows 머신에서 배치로 돌려 결과를 PostgreSQL에 적재하고, 분석측은 그 DB를 읽습니다.

```text
┌──────────────────────── Windows 머신 ────────────────────────┐
│  키움 OpenAPI+ (COM/DLL, 로그인 세션 필요)                    │
│        │  pykiwoom block_request (TR: OPT10081/10059/10001,   │
│        │                              OPT20006/20004)         │
│        ▼                                                      │
│  price-collector (배치 CLI · 엔드포인트 없음)                 │
│     python -m app.main          ← 종목 주가/수급             │
│     python -m app.sector_main   ← 업종 지수                  │
│     · Windows 작업 스케줄러로 장 마감 후 자동 실행            │
└────────┬─────────────────────────────────────────────────────┘
         │  psycopg (DATABASE_URL) · 쓰기 전용
         ▼
┌──────────────────────── EC2 / 관리형 ────────────────────────┐
│  PostgreSQL                                                   │
│     ohlcv_data · sector_ohlcv · collector_runs               │
│        ▲                                                      │
│        │  DB 조회만 (수집기를 직접 호출하지 않음)             │
│  agent-worker (:8011) · main-server (:8000) · web (:3000)    │
│     ← 이쪽은 FastAPI/Next.js, 엔드포인트 있음, Linux/Docker   │
└──────────────────────────────────────────────────────────────┘
```

### 엔드포인트 유무 정리

| 구성요소 | 엔드포인트 | 실행 위치 | 트리거 |
| --- | --- | --- | --- |
| main-server | `:8000` (FastAPI) | EC2 / Docker | HTTP |
| agent-worker | `:8011` (FastAPI) | EC2 / Docker | HTTP |
| web | `:3000` (Next.js) | EC2 / Docker | HTTP |
| **price-collector** | **없음 (배치 CLI)** | **Windows 머신** | **작업 스케줄러 / 수동 실행** |

### 실제 구동에 필요한 런타임 (코드와 별개)

코드는 구현 완료 상태이며, 실수집을 돌리려면 다음 환경이 필요합니다.

1. 상시 가동 **Windows 머신** (예: AWS EC2 Windows 인스턴스)
2. **키움 OpenAPI+ 설치 + 키움 계좌 로그인 세션** (대화형 또는 자동로그인)
3. 의존성: `pip install -e ".[kiwoom]"` (pykiwoom, pandas)
4. `DATABASE_URL`을 공유 PostgreSQL(EC2)로 지정
5. `python -m app.main --all` / `python -m app.sector_main` → 작업 스케줄러로 장 마감 후 예약

> 분석측에서 "API로 수집을 트리거"하려면, **키움 호출은 반드시 Windows에서** 일어나야 하므로 트리거용 HTTP 서버도 그 Windows 머신에 두어야 합니다. EC2(Linux)에서 키움을 직접 호출하는 구성은 불가능합니다.
