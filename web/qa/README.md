# 로컬 브라우저 QA — browser-harness

이 디렉터리는 [`browser-use/browser-harness`](https://github.com/browser-use/browser-harness)로
signal-alpha 웹 프론트엔드를 **로컬에서** 자연어 기반 E2E/QA 하는 자산이다.

> **browser-harness란?** LLM(코딩 에이전트)이 CDP로 실제 Chrome을 직접 조종하는 자가치유(self-healing)
> 하니스다. Playwright/Cypress 같은 결정론적 회귀 프레임워크가 **아니며**, 항상 LLM이 루프 안에 있어야
> 동작한다. 따라서 무인 CI 게이트가 아니라 **개발자가 시나리오로 구동하는 적응형 QA**다.
> 결정론적 회귀 게이트가 필요하면 Playwright를 별도 도입할 것(범위 밖).

## ⚠️ 인증 제약 (반드시 이해할 것)

프론트(`web/src/lib/portone.ts`)·백엔드(`services/main-server/app/core/portone.py`) 모두 PortOne를
**항상 real 모드**로만 호출한다(dev 폴백을 의도적으로 제거). 결과적으로:

- **실 SMS 본인인증 / 실 카드 결제 위젯 자체는 자동화 불가** → 수동 관찰로만 검증.
- 그래서 인증이 필요한 플로우는 **DB 시드 + `sa_refresh` 쿠키 주입**으로 로그인 상태를 만든다
  (앱 코드 변경 없음). 구독중 상태도 결제 우회로 DB에 직접 시드한다.

| 영역 | 자동화 | 방법 |
|---|---|---|
| 홈 / 검색 / 리포트(전체 공개) / 소스 상세 | ✅ 무인 | 인증 불필요 |
| 관심종목·마이페이지·구독중 UI·저널 | ✅ | 시드 + 쿠키 주입 |
| 관리자 대시보드 | ✅ | 관리자 계정 시드 후 이메일/비번 로그인 |
| PortOne 본인인증 로그인/가입, 카드 결제 위젯 | ❌ 수동만 | real 전용 |

## 사전 준비 (1회)

```bash
# browser-harness 설치(Python 3.12 자동) + 스킬 등록은 이미 완료되어 있어야 한다.
uv tool install --python 3.12 --upgrade --force browser-harness
browser-harness --doctor    # version/python 확인
```

Chrome는 **원격 디버깅**이 켜져 있어야 한다. browser-harness 명령 실행 시 daemon이 못 붙으면
`chrome://inspect/#remote-debugging` 을 열어 "Allow remote debugging for this browser instance"를
체크한다(권한 팝업이 뜨면 Allow).

## 스택 기동 (QA 세션마다)

DEV DB는 Docker Postgres(`localhost:55432`)다. **Docker Desktop이 실행 중이어야 한다.**

```bash
# 1) DB + 마이그레이션/seeds (레포 루트)
docker compose up -d postgres
docker compose run --rm db-migrate apply --seeds   # subscription_plans(002) 포함

# 2) 백엔드 (별도 터미널)
cd services/main-server && uv run uvicorn app.main:app --reload   # :8000, GET /health

# 3) 프론트 (별도 터미널)
cd web && npm install && npm run dev                              # :3000
```

대안: `docker compose up -d postgres main-server web` 로 한 번에 띄울 수도 있다(마이그레이션 먼저).
프론트는 기존 `web/.env.local`(API base=http://localhost:8000)을 그대로 쓴다 — **이 방식은 env 변경이
필요 없다**(빈 PortOne 키에 의존하지 않으므로).

## 인증 상태 만들기 (시드 + 쿠키 주입)

```bash
# 무료 사용자 시드(관심종목/마이페이지용)
uv run --package signal-alpha-main-server python services/main-server/scripts/seed_e2e_user.py
# 구독중 사용자 시드(저널 등 구독 전용 UI용)
E2E_SUBSCRIBE=1 uv run --package signal-alpha-main-server python services/main-server/scripts/seed_e2e_user.py
```

출력 JSON의 `refresh_token` 을 복사해 browser-harness로 쿠키를 주입한다:

```bash
browser-harness <<'PY'
R = "여기에-시드가-출력한-refresh_token"
new_tab("http://localhost:3000")
wait_for_load()
cdp("Network.enable")
cdp("Network.setCookie", name="sa_refresh", value=R,
    domain="localhost", path="/api/auth", httpOnly=True, secure=False, sameSite="Lax")
new_tab("http://localhost:3000/mypage")   # 재진입 → AppShell hydrate → /api/auth/refresh
wait_for_load()
print(page_info())                         # 로그인 상태(마이페이지) 확인
PY
```

동작 원리: access 토큰은 인메모리 전용(`web/src/lib/session.ts`)이라 직접 주입 불가. 대신 `sa_refresh`
쿠키만 심으면 부팅 hydrate(`AppShell.tsx` → `refreshSession()`)가 access 토큰을 재발급한다.

## 시나리오 실행

`scenarios/` 의 각 markdown을 browser-harness 스킬을 켠 코딩 에이전트에 자연어로 구동한다.
실패하면 self-heal로 보완되는지 관찰하고, 안정화된 절차/헬퍼는 여기(`web/qa/`)에 고정한다.
(task-specific 헬퍼는 `$BH_AGENT_WORKSPACE/agent_helpers.py` 에 쌓이므로 재사용 가치가 있으면 복사.)

## 정리

- 시드 데이터는 로컬 DEV DB에만 들어간다. 초기화하려면 `docker compose down -v` 로 볼륨 삭제 후 재적용.
- 클라우드 브라우저(Browser Use)는 로컬 QA에 불필요 — 사용하지 않는다.
