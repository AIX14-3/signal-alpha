# 4분할 배포 체크리스트 (worker / backend / frontend / db)

배포를 워커·백엔드·프론트·DB 4개 컴포넌트로 나눌 때 문제없이 올리기 위한 점검 목록.
이 브랜치(`chore/deploy-4-component`)에서 준비한 사항과, 배포 시점에 사람이 채워야 하는
값/단계를 구분한다.

## 컴포넌트 매핑

| 컴포넌트 | 서비스 | 이미지 | 공개 | 비고 |
| --- | --- | --- | --- | --- |
| worker | `agent-worker` (+`analyzer`) | `services/agent-worker/Dockerfile` | ❌ 내부 전용 | `/internal/*` 무인증, advisory lock → **단일 인스턴스**·공개 도메인 금지 |
| backend | `main-server` | `services/main-server/Dockerfile` | ✅ | 사용자 대면 API. PortOne/OAuth/구독 |
| frontend | `web` | `web/Dockerfile.prod`(자체호스팅) 또는 Vercel | ✅ | dev `web/Dockerfile` 는 배포 부적합 |
| db | `postgres`(로컬) → managed | pgvector/pg16 | ❌ | 배포 시 Neon/RDS 등 managed 로 대체 |

## 빌드·기동

```bash
# DB 마이그레이션 선실행(서비스 기동 전 1회) — init container / pre-deploy hook 로 동일 적용
docker compose run --rm migrate

# 로컬 전체(프론트 dev)
docker compose up

# 프론트 프로덕션 빌드까지 포함해 검증
docker compose --profile prod up web-prod main-server agent-worker postgres
```

프론트 자체호스팅 이미지 단독 빌드(NEXT_PUBLIC_* 은 **빌드 타임 인라인**이라 build-arg 필수):

```bash
docker build -f web/Dockerfile.prod \
  --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.example.com \
  -t signal-web ./web
```

## 컴포넌트 간 배선 (env)

| 방향 | 변수 | 로컬 기본값 | 배포 시 |
| --- | --- | --- | --- |
| 모든 서비스 → db | `DATABASE_URL` | compose postgres | managed 엔드포인트로 교체. SSL 은 `DB_SSLMODE=require` 권장 |
| backend → worker | `AGENT_WORKER_BASE_URL` | `http://agent-worker:8011` | 내부 DNS/IP |
| frontend → backend | `NEXT_PUBLIC_MAIN_API_BASE_URL` | `http://localhost:8000` | **빌드 타임** 실제 백엔드 URL |
| backend CORS | `CORS_ALLOW_ORIGINS` | `http://localhost:3000` | 프론트 실제 도메인 추가 |

> `DB_SSLMODE`/`DB_SSL` 는 worker 스크립트·data-access 풀·`migrate.py` 가 공통으로 읽는다
> (`fix/db-connection-ssl` 브랜치 참고). 로컬은 미설정(자동 비SSL), managed 는 `require`.

## 배포 전 반드시 교체할 시크릿/값

- [ ] `AUTH_SECRET_KEY` — dev 기본값(`dev-main-server-secret-change-me`) 교체
- [ ] `PORTONE_API_SECRET` — 미설정 시 dev(모의) 모드. 실결제/실취소엔 실키 필수
- [ ] `DART_API_KEY` — 신호 파이프라인 E2E 에 필요
- [ ] `GEMINI_API_KEY`(+`SYNTHESIS_USE_LLM=true`) — LLM 종합을 켤 경우(`feat/mvp-llm-synthesis` 참고)
- [ ] `KIWOOM_*` — 모의키 만료(2026-09-06) 추적. 실전 전환 시 `KIWOOM_API_BASE=https://api.kiwoom.com`

## 헬스체크

- `main-server`/`agent-worker` `/health` 는 이제 풀에서 `SELECT 1` 로 **DB 연결까지 점검**한다.
  DB 미초기화/끊김 시 503(`db: uninitialized|error`). 로드밸런서/ECS 헬스체크에 그대로 사용.
- DB 정상: `{"status":"ok","db":"ok",...}` / 비정상: HTTP 503.

## 운영 제약(주의)

- worker 는 가격 수집/ops 데몬이 advisory lock 을 잡으므로 **1 인스턴스만** 띄운다.
- worker `/internal/*` 는 인증이 없으므로 **공개 도메인/ALB 미부착**(GitHub Actions/cron 으로 호출).
- 프론트는 Vercel 또는 `web-prod` 중 택1. 둘 다 `NEXT_PUBLIC_MAIN_API_BASE_URL` 빌드 타임 주입.
