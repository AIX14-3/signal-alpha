# Signal Alpha 빠른 MVP 구성 방안 (Fast Track)

> 작성일 2026-06-24 · 짝 문서: [deploy-backlog.md](./deploy-backlog.md)(정식 출시·ML 포함)
> 목적: **이미 완료된 것만으로 최단 시간에 배포**하는 경로. 정식 출시는 이 위에 얹는다.

## 한 줄 요약

ML 추론을 **폴백(consensus-only) 모드로 두고** 데이터 백필·GPU·재학습 의존성을 끊으면, 지금 코드 상태로 **DART 규칙기반 시그널 + 포트원 결제 + web**을 곧장 띄울 수 있다. 핵심 차단요소는 ML이 아니라 **배포 인프라 + 최소 관측(Sentry)** 둘뿐이다.

## 핵심 판단

정식 출시 백로그의 P0는 6개였지만, MVP에서는 그중 **3개만 진짜 차단요소**다.

| 정식 P0 | MVP 처리 | 이유 |
|---|---|---|
| P0-1 OHLCV 120일 백필 | **연기** | ML 없으면 불필요. 파이프라인이 OHLCV 없을 때 consensus-only로 폴백하도록 이미 설계됨 |
| P0-2 실데이터 E2E 발행 | **유지(필수)** | 리포트가 실제로 나와야 서비스가 성립 |
| P0-3 포트원 real 결제 | **유지(필수)** | 매출 발생 = 결제 실연동 필수 |
| P0-4 로깅+Sentry | **유지(축소)** | Sentry만. 구조화 로깅·메트릭은 연기 |
| P0-5 배포 자동화 | **유지(축소)** | 자동화 대신 수동 배포 스크립트로 시작 |
| P0-6 시크릿+헬스체크 | **유지(축소)** | 시크릿은 VM env로, 헬스체크 DB확인만 |

> 핵심 결정: **"MVP는 규칙기반(DART 중심) 시그널로 출시하고, ML은 폴백으로 조용히 동작시킨 뒤 데이터가 쌓이면 켠다."** 사용자에게는 ML on/off가 보이지 않음(수치는 동일 경로, ML은 변동성 피처 보강일 뿐).

---

## MVP 스코프

### 넣는다 (이미 완료 → 그대로 사용)
- **인증/가입**: 포트원 본인인증 + JWT + 소셜 OAuth
- **결제/구독**: 포트원 결제, 무료 3회 쿼터 + 단일 9900 구독
- **시그널 파이프라인**: DART 수집→정규화→분석→AGGREGATE→(LLM 설명)→RISK_VETO 발행. **DART-only MVP aggregator로 충분**
- **web**: home/search/report/pricing/login/signup/mypage/admin
- **관심종목**: 무제한
- **DB**: 현재 마이그레이션 그대로

### 뺀다 / 끈다 (출시 후로)
- ML 실추론(→ consensus-only 폴백), GPU 모델, 메타러너 재학습
- OHLCV 120일 백필, Price scoring source 편입, multi-source 가중치
- 알림 시스템, 백테스팅, 대시보드 상세, 데이터 보관 정책
- 메트릭/Prometheus, rate limit(또는 최소만), CSRF 고도화

### 환경변수 설정 (MVP 모드)
```
SYNTHESIS_USE_LLM=false      # LLM 설명도 선택 — 결정론 narrative 폴백으로 시작(비용 0)
                             #   → 여유되면 true + gemini로 켜기
# ML_GATE_PASSED_MODELS 는 두되, OHLCV 없으면 자동 폴백되므로 무해
PORTONE_API_SECRET=<real>    # 결제는 real 필수
AUTH_SECRET_KEY=<교체>       # dev 기본값 반드시 교체
```

---

## 인프라 (가장 단순한 안)

**단일 클라우드 VM + docker-compose + 관리형 PostgreSQL(Neon free/RDS small)**

- web → Vercel 무료 배포 (또는 같은 VM)
- main-server + agent-worker → VM에서 docker-compose up
- DB → Neon(pgvector 지원·무료 시작) 또는 RDS
- 도메인 + Caddy/nginx 리버스 프록시(HTTPS 자동)
- 시크릿 → VM의 `.env`(권한 600) 로 시작, 매니저는 나중에

> K8s·ECS·IaC·Terraform 전부 출시 후로. 지금은 `git pull && docker compose up -d`면 충분.

---

## 실행 순서 (권장 ~1주)

1. **인프라 띄우기** (`infra/biop`): VM + 관리형 PG 프로비저닝, 마이그레이션 적용(드리프트 0), web 별도 배포. → 빈 서비스가 https로 응답.
2. **Sentry 연동** (`infra/biop`): 두 서비스 main.py에 Sentry init만. (구조화 로깅은 생략)
3. **실데이터 발행 검증** (`worker/biop`+`aggregator/jolly`): 실 DART_API_KEY로 N종목 e2e → `final_signals.is_published=true` → web 리포트 렌더. RISK_VETO 종목 미발행 확인.
4. **포트원 real 결제 검증** (`main-server/jolly`+`web/seoeunjin`): 본인인증→가입→결제→구독→열람→취소 1회 통과.
5. **헬스체크 + 수동 배포 스크립트** (`infra/biop`): `/health` DB확인 추가, 배포 1줄 스크립트(`git pull && docker compose up -d --build`)·롤백(이전 태그) 메모.
6. **소프트 런칭**: 지인/팀 한정 오픈 → 관측하며 정식 출시 백로그(P1) 착수.

---

## Go-Live 체크리스트 (MVP)

- [ ] 관리형 DB에 마이그레이션 적용·드리프트 0
- [ ] web → main-server → agent-worker https 연결, CORS 정상
- [ ] 실 DART 키로 발행 리포트 1건 이상 web 렌더
- [ ] RISK_VETO 치명키워드 종목 미발행 확인
- [ ] 포트원 real 모드 결제·구독·취소 1회 통과
- [ ] `AUTH_SECRET_KEY` 등 dev 기본값 전부 교체
- [ ] Sentry에 의도적 에러 1건 수신 확인
- [ ] `/health` DB 단절 감지
- [ ] 면책 문구(투자조언 금지 NOTICE) 모든 리포트 노출
- [ ] 수동 배포·롤백 1회 리허설

---

## MVP → 정식 출시 승격 경로

MVP 안정화 후 [deploy-backlog.md](./deploy-backlog.md) 순서로:
1. **P0-1** OHLCV 백필 → ML 실추론 켜기(`ml_inferences` 폴백→실값, `meta_signals` stacking)
2. **P1-5** GPU 모델 검증 편입
3. **P1-1~P1-4** 보안 하드닝·E2E 테스트·메트릭/알림·마이그레이션 정리
4. **P2** multi-source aggregation·재학습 자동화·대시보드·알림

> ML을 폴백으로 깔아둔 덕에 승격은 **데이터 백필 + 환경변수 토글**에 가깝고, 코드 재작업이 거의 없다.

---

## 리스크 / 주의

- **시그널 품질**: MVP는 DART 규칙 + consensus-only. 변동성 ML 피처가 빠지므로 "정밀도"보다 "동작·신뢰 가능한 근거 제시"에 메시징 집중.
- **LLM off로 시작 시**: 리포트 설명이 결정론 narrative(템플릿형). 어색하면 P0 끝나고 `SYNTHESIS_USE_LLM=true`로 토글.
- **관리형 DB pgvector**: Report RAG·dart_chunks 임베딩 사용하므로 pgvector 확장 지원 DB 필수(Neon·RDS 모두 지원, 확인 후 선택).
- **키움 모의키 만료 2026-09-06**: MVP에서 Price 데몬을 꺼두면 무관하나, 켤 거면 만료 추적.
