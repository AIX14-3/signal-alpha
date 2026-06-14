# 토스증권 Open API 타당성 검증 스파이크

signal-alpha의 데이터 소스를 키움 → **토스증권 Open API**로 전환·확장하는 방향이
프로젝트에 적합한지 **실제 라이브 호출**로 검증하는 독립 PoC.
운영 코드(`services/`, `database/`)는 건드리지 않으며 DB에도 쓰지 않는다.

## 검증 대상
- A. 인증 (`POST /oauth2/token`, OAuth2 client-credentials)
- B. 국내 실시간 시세 (`GET /api/v1/prices`)
- C. **10년 일봉 깊이** (`GET /api/v1/candles`) ← 가장 중요한 검증 (10년 백필 실현성)
- D. 미국 시세 (`GET /api/v1/prices` + `/market-calendar/US`)
- E. 환율 (`GET /api/v1/exchange-rate`)
- F. 레이트리밋 실측 (prices 연속 호출)

## 실행
```bash
# 1) 자격증명 주입 — 레포 루트 .env 또는 환경변수
#    TOSS_CLIENT_ID=...
#    TOSS_CLIENT_SECRET=...
#    TOSS_API_BASE=https://openapi.tossinvest.com   (기본값)

# 2) 실행 (이 디렉터리에서)
cd spikes/toss-feasibility
python run_spike.py
# 또는 워크스페이스에서:  uv run python run_spike.py
```

의존성은 워크스페이스에 이미 있는 `httpx`, `python-dotenv`만 사용한다.

## 산출물
- 콘솔에 A~F PASS/FAIL 요약
- `FINDINGS.md` 자동 생성 — 실제 응답 샘플 + go/no-go 체크리스트
  (응답 필드 형태를 보고 `StockSnapshot` 매핑·스키마 확장 여부를 판단)

## 주의
- 시세 엔드포인트의 정확한 쿼리 파라미터 형태는 문서에 일부만 명시되어 있어,
  스파이크가 몇 가지 후보 파라미터를 순차 시도하고 **서버가 받아들인 형태**를
  FINDINGS에 기록한다. 결과를 보고 실제 파라미터명을 확정하면 된다.
- 클라이언트(`toss_client.py`)는 기존
  `services/price-collector/app/kiwoom/{auth,rest_client}.py` 패턴을 본떠
  작성되어, go 판정 시 `TossCollector`로 그대로 승격하기 쉽다.
