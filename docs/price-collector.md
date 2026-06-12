# Price 수집 데몬 (Kiwoom REST · 실시간 폴링)

키움증권 **REST API**(App Key/Secret + OAuth)로 타깃 종목의 주가 스냅샷을
장중에 주기적으로 수집해 PostgreSQL에 적재합니다.
COM/DLL 의존성이 없어 **리눅스·Docker에서 실행됩니다.**
(이전 OpenAPI+/pykiwoom 배치 수집기는 PR #26·#32·#33·#52 revert로 제거)

별도 서비스가 아니라 **agent-worker 안에서 lifespan 백그라운드 asyncio 태스크**로
돕니다. 코드는 `services/agent-worker/app/collectors/price/`에 있고, 진입점은
`runner.py`(`run_daemon`/`supervise_daemon`)입니다.

## 동작 방식

```text
stocks (is_target = TRUE)            ← 수집 대상은 DB 스위치로 결정 (하드코딩 없음)
        │
        ▼
폴링 루프 (장중 09:00~15:30 KST, 기본 60초 간격)
        │  ka10001 주식기본정보 (현재가·OHLC·거래량·시총·PER/PBR/EPS/BPS/ROE/ROA)
        ▼
price_snapshots  ← 장중 시점별 스냅샷 (stock_id, captured_at 유니크)
ohlcv_data       ← 당일 행 UPSERT (close = 현재가, 수급 컬럼은 보존)
        │
        ▼
장 마감 +30분: ka10059 투자자 순매수 확정치 → ohlcv_data.foreign_net/institution_net
collector_runs   ← 세션 단위 실행 로그 (collector_type = 'PRICE')
```

- 한 종목 실패가 전체 사이클을 멈추지 않습니다 (실패 카운트만 기록).
- 레이트리밋: 호출 간 최소 간격 가드 (`KIWOOM_MIN_REQUEST_INTERVAL_SEC`, 기본 0.25초).
- 휴장일(공휴일)은 별도 처리하지 않습니다 — 주말/장시간 게이트만 적용.
- 데몬이 예기치 못하게 죽으면 `supervise_daemon`이 60초 후 재기동합니다.
- 서버 종료 시 열린 폴링 세션은 `collector_runs`에 마감 처리 후 종료됩니다.

## 실행

agent-worker가 뜨면 데몬도 함께 뜹니다 (`PRICE_COLLECTOR_ENABLED=true` 기본).

```powershell
# 테스트
cd services/agent-worker
uv run pytest tests/price_collector

# 단발 수집 (구 --once / --flows 대체, 연결/필드 매핑 검증용)
curl -X POST localhost:8011/internal/price/collect -H "Content-Type: application/json" -d '{"mode": "snapshot"}'
curl -X POST localhost:8011/internal/price/collect -H "Content-Type: application/json" -d '{"mode": "flows"}'
```

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `PRICE_COLLECTOR_ENABLED` | `true` | 수집 데몬 on/off (테스트/로컬에서 끌 때 `false`) |
| `KIWOOM_APP_KEY` / `KIWOOM_APP_SECRET` | — | REST API 앱 키/시크릿 (필수) |
| `KIWOOM_API_BASE` | `https://mockapi.kiwoom.com` | 모의투자 도메인. 실전 키 발급 후 `https://api.kiwoom.com` |
| `PRICE_POLL_INTERVAL_SEC` | `60` | 장중 폴링 간격 (초) |
| `PRICE_FLOW_DELAY_AFTER_CLOSE_MIN` | `30` | 장 마감 후 수급 확정치 조회까지 대기 (분) |
| `KIWOOM_MIN_REQUEST_INTERVAL_SEC` | `0.25` | 호출 간 최소 간격 |
| `DATABASE_URL` | — | 공유 PostgreSQL (필수 — 없으면 데몬도 뜨지 않음) |

## 주의

1. **agent-worker는 단일 uvicorn 워커 전제**입니다. 멀티 워커(`--workers N`)로 띄우면
   데몬이 워커 수만큼 중복 기동됩니다. (`--reload`는 프로세스 교체라 안전)
2. **현재 발급 키는 모의투자용** (만료 2026-09-06). 모의 도메인은 시세 범위가
   제한될 수 있으므로 실데이터 적재 시 실전투자용 키 발급이 필요합니다.
3. ka10001/ka10059 응답 필드명은 키움 REST 문서 기준으로 매핑했으며
   (`app/collectors/price/*.py`의 `*_FIELDS` 상수), 모의 도메인 실호출로 검증 후
   필요 시 상수만 수정하면 됩니다. 검증: `POST /internal/price/collect`
4. 120일 과거 일봉 백필(ka10081)·주/월/년봉·업종 지수는 **후속 작업**입니다.
   PRICE 분석기는 21영업일 이상 누적돼야 점수를 산출하므로, 백필 전까지는
   `insufficient_history` 상태가 정상입니다.
