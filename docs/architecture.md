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

## 데이터 수집기 런타임 (Price Collector — Kiwoom REST 실시간 폴링)

주가 수집기(`services/price-collector`)는 키움증권 **REST API**(App Key/Secret + OAuth)를
사용하므로 다른 서비스와 같은 **리눅스/Docker 환경에서 실행됩니다.**
(이전 OpenAPI+/COM 기반 Windows 배치 수집기는 PR #26·#32·#33·#52 revert로 제거)

```text
┌──────────────────── Docker / Linux (EC2) ─────────────────────┐
│  price-collector (장시간 데몬 · python -m app.main)            │
│     ① stocks.is_target = TRUE 종목을 DB에서 조회                │
│     ② 장중(평일 09:00~15:30 KST) 기본 60초 간격 폴링            │
│        └ REST ka10001 주식기본정보 (현재가·OHLC·시총·PER 등)    │
│     ③ 장 마감 +30분: ka10059 투자자 순매수 확정치 1회           │
│        │                                                       │
│        ▼  asyncpg                                              │
│  PostgreSQL                                                    │
│     price_snapshots  ← 장중 시점별 스냅샷                       │
│     ohlcv_data       ← 당일 행 UPSERT + 수급 확정치             │
│     collector_runs   ← 세션 단위 실행 로그 (PRICE)              │
│        ▲                                                       │
│        │  DB 조회만 (수집기를 직접 호출하지 않음)                │
│  agent-worker (:8011) · main-server (:8000) · web (:3000)      │
└────────────────────────────────────────────────────────────────┘
         ▲
         │  HTTPS (OAuth Bearer + api-id 헤더)
   키움 REST API (모의: mockapi.kiwoom.com / 실전: api.kiwoom.com)
```

### 원칙

- 수집 대상은 `stocks.is_target` 스위치로만 결정한다 (코드 하드코딩 금지).
- 분석측(agent-worker PRICE 분석기)은 키움 API를 직접 호출하지 않고 DB만 읽는다.
- 실시간 수집은 타깃 종목에 한정한다 — 전 종목 실시간 모니터링은 후순위 확장.
- 120일 과거 일봉 백필(ka10081)·주/월/년봉·업종 지수 수집은 후속 작업이다.
  백필 전까지 PRICE 분석기는 `insufficient_history` 상태가 정상이다.

상세 실행/환경 변수는 `services/price-collector/README.md`,
수집 데이터 명세는 `docs/kiwoom-rest-spec.md` 참고.
