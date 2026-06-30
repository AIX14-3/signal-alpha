# Signal Alpha 배포 백로그 (정식 출시 / ML 포함)

> 작성일 2026-06-24 · 레포: https://github.com/AIX14-3/signal-alpha

## Context

오늘 풀스택 설계가 완료되었고(백엔드 API·프론트 스펙·디자인·DB 동시 재작성), 각 영역 담당자가 수집기/분석기/ML·DL을 흐름에 맞게 진행 중이다. 기능 구현은 상당히 진척되었으며 — 핵심 게이트형 파이프라인(COLLECT→NORMALIZE→ANALYZE→ML_INFER→META_COMBINE→AGGREGATE→SYNTHESIZE→RISK_VETO)이 이미 코드로 동작하는 수준 — **"정식 출시(ML 포함)"를 막는 것은 신규 기능이 아니라 검증·데이터 백필·운영 인프라(로깅/모니터링/배포 자동화/시크릿)** 이다.

이 문서는 배포까지 남은 일을 우선순위(P0 배포 차단 / P1 출시 직후 필수 / P2 출시 후 개선)로 정리하고, 영역→담당자 매핑과 완료조건을 붙여 팀 백로그로 바로 쓰도록 한다.

목표 결과물: 실데이터로 종목 검색→리포트 발행→결제→구독 열람이 프로덕션 환경에서 안정 동작하고, 장애 발생 시 추적 가능한 상태.

---

## 현재 상태 스냅샷

### 완료 (배포에 추가 작업 거의 불필요)
- **main-server API** 13개 라우터 전부 구현 (auth/payments/reports/signals/subscriptions/watchlists/journals/admin/dashboard/analytics/health)
- **인증·결제** 포트원 V2 본인인증 단일 + JWT(access 30m/refresh 14d, refresh는 SHA256 해시 저장·1회용) + 소셜 OAuth(naver/google/kakao) + 무료 3회 쿼터·단일 구독
- **게이트형 분석 파이프라인** COLLECT/NORMALIZE/ANALYZE(DART·Report·Alternative) → ML_INFER → META_COMBINE → AGGREGATE(final_signals·consensus) → SYNTHESIZE(LLM 설명, 수치 불변) → RISK_VETO(치명 키워드·정제루프) **모두 핸들러 구현·단위테스트 존재**
- **수집기 7종**(dart/report/hiring/patent/datalab/price/sec[폐기]), **분석기** 소스별 구현
- **web 프론트** 10개 페이지(home/login/signup/report/source-detail/auth-callback/admin/pricing/mypage/dashboard) + Zustand 5 스토어 + apiClient
- **DB** 마이그레이션 027개(단일 구독 모델까지) + 시드 7종, smoke test CI
- **Docker** 3종 Dockerfile + docker-compose(로컬), CI(ci.yml/datalab-daily)

### 진행중 / 검증 필요
- **Price 데몬** Kiwoom REST 폴링 구현됐으나 **120일 OHLCV 백필 미완** → Price analyzer·ML 추론 입력 부족
- **ML 추론 모델 검증** CPU 4종(ewma/har_rv/garch/lightgbm) 화이트리스트, **GPU 2종(kronos/chronos2) 실 GPU 검증 대기**
- **메타러너 가중** `app/ml/artifacts/meta_learner.json` 수동 산출(vol-benchmark 별도 레포), 자동 재학습 없음
- **Aggregator** 현재 DART-only MVP — PRICE를 scoring source로 미포함, source별 가중치 단순평균
- **대시보드** 페이지 껍데기, 세부 조회 로직 진행중

### 미착수 / 취약 (운영 리스크)
- **로깅/모니터링/에러트래킹** 기본 logging만, 구조화·Sentry·메트릭 전무 → **프로덕션 장애 추적 불가**
- **프로덕션 배포 자동화** docker-compose 로컬만, prod 파이프라인/IaC 없음
- **시크릿 관리** .env.example만 있고 시크릿 매니저 미연동
- **보안 하드닝** rate limit(로그인 brute force)·CSRF·헬스체크 DB 확인 미흡
- **테스트** 단위 101개 있으나 커버리지 미측정, **E2E(main-server↔agent-worker 실연동) 미검증**

---

## 영역 → 담당자 매핑 (보드 규칙 기준)

| 영역 | 담당자 | 영역 | 담당자 |
|---|---|---|---|
| worker(수집/분석/파이프라인) | biop | main-server / aggregator | jolly |
| datalab | iseul | hiring / qa | ArtRS |
| web | seoeunjin | db / infra | biop |
| patent / report | 미배정 → 출시 전 지정 필요 | | |

> 인프라·배포·모니터링은 db-infra(biop)로 묶되, 분량이 커서 patent/report 미배정 인원을 인프라 보강에 투입 권장.

---

## P0 — 배포 차단 (이게 안 되면 출시 불가)

ML 포함 정식 출시이므로 "파이프라인이 실데이터로 돌고, 결제가 실연동되고, 장애를 볼 수 있는" 것이 최소 조건.

### P0-1. OHLCV 120일 백필 + Price → ML 입력 검증 · `worker/biop`
- **왜**: ML_INFER(`app/ml/inference.py`)는 `ohlcv_data` 400세션 lookback을 읽음. 역사 데이터 없으면 ML/메타러너가 전부 폴백(consensus-only)으로 떨어져 "ML 포함" 출시의 의미가 사라짐.
- **완료조건**: 대상 종목(코스피20+관심권) 120일 이상 OHLCV 적재, ML_INFER가 폴백 아닌 실추론으로 `ml_inferences` 채움 확인, META_COMBINE가 `equal_fallback` 아닌 `stacking` 경로 동작.
- 참고: `services/agent-worker/app/collectors/price/runner.py`, `tools/intraday` 수집기, 키움 모의키=실시세(mockapi).

### P0-2. 실데이터 E2E 파이프라인 검증 (수집→발행) · `worker/biop` + `aggregator/jolly`
- **왜**: 단계별 단위테스트는 있으나 실데이터로 끝까지(COLLECT→…→final_signals 발행) 흐르는 검증 미확인. 출시 전 1종목이라도 실제 발행 리포트가 나와야 함.
- **완료조건**: 실 DART_API_KEY로 N개 종목 e2e 실행 → `final_signals.is_published=true` 리포트가 web `/report/[ticker]`에 정상 렌더, RISK_VETO 치명키워드 종목은 미발행 확인.
- 참고: `POST /internal/dart/e2e`, aggregation/synthesis/risk_veto tasks.

### P0-3. 포트원 결제·본인인증 실연동(real 모드) 검증 · `main-server/jolly` + `web/seoeunjin`
- **왜**: 현재 키 미설정 시 dev 모의모드. 실 매출이 발생하는 정식 출시는 real 모드 검증이 필수.
- **완료조건**: 포트원 콘솔 store/channel 키 발급 → 본인인증→가입→결제(9900)→구독 활성→무제한 열람→취소 전 구간 real 모드 1회 통과. 결제 실패/중복결제 가드 확인.
- 참고: `app/core/portone.py`, `routes/payments.py`, `routes/subscriptions.py`, `docs/spec/third-party-integration-setup.md`.

### P0-4. 구조화 로깅 + 에러트래킹(Sentry) · `infra/biop` (+미배정 인원)
- **왜**: 현재 print/기본 logging뿐. 프로덕션에서 5xx·데몬 다운·외부 API 장애를 추적할 수단이 전혀 없음 → 운영 불가 수준.
- **완료조건**: `app/core/logging.py`(JSON 포맷, request_id) 신규, 두 서비스 main.py에 Sentry init·미들웨어, 수집기/분석기 실패가 구조화 로그로 남음. agent-worker lifespan 데몬 시작/실패 로그 포함.

### P0-5. 프로덕션 배포 인프라 확정 + 자동화 · `infra/biop`
- **왜**: docker-compose 로컬만 존재. 배포 대상·방법이 없으면 출시 자체가 불가.
- **인프라 추천안(미정 → 아래로 제안)**: **단일 클라우드 VM + docker-compose(prod) + 관리형 PostgreSQL(Neon 또는 RDS, pgvector 지원)**. 이유: 팀 규모·비용·ML CPU 추론(GPU는 게이트로 선택적) 고려 시 K8s는 과함. web은 Vercel 분리 배포 가능. 추후 트래픽 증가 시 ECS로 승격.
- **완료조건**: prod용 compose/env 분리, DB는 관리형으로 이전(마이그레이션 적용·드리프트 0), 배포용 GitHub Actions(이미지 빌드→레지스트리 푸시→VM pull/up), 헬스체크 통과, 롤백 절차 문서화.
- 대안: (B) AWS ECS/K8s+IaC — 운영 성숙도 높지만 셋업 비용 큼. (C) 단일 VM all-in-one(DB 포함) — 가장 저렴하나 DB 내구성 위험.

### P0-6. 시크릿 관리 + DB 헬스체크 · `infra/biop`
- **왜**: API 키·AUTH_SECRET_KEY를 평문 .env로 prod 운용하면 유출 위험. `/health`가 프로세스 생존만 확인하면 DB 단절을 못 잡음.
- **완료조건**: prod 시크릿을 시크릿 매니저(또는 CI 암호화 env)로 주입, `AUTH_SECRET_KEY` 등 dev 기본값 교체, `/health`에 DB 연결 확인 추가, 외부키 만료(키움 모의 2026-09-06 등) 추적 메모.

---

## P1 — 출시 직후 필수 (출시 주간 내 처리)

### P1-1. 인증 보안 하드닝 (rate limit / CSRF) · `main-server/jolly`
- 로그인·토큰 발급 brute force 방어(slowapi 등 rate limit), POST 변경 엔드포인트 CSRF/Origin 검증. 완료조건: 로그인 N회 초과 시 차단, 부정 Origin 거부.

### P1-2. E2E 통합 테스트 + CI 커버리지 게이트 · `qa/ArtRS`
- main-server↔agent-worker 실연동 시나리오 테스트 1식, pytest coverage 측정·CI 리포트. 완료조건: 핵심 플로우(가입→발행→결제) E2E 1개 이상 CI에서 green, 커버리지 수치 노출.

### P1-3. 메트릭/알림 · `infra/biop`
- FastAPI prometheus-client 엔드포인트, 핵심 알림(Price/Hiring 데몬 다운, 5xx율>5%, DB풀 포화, DataLab 일1000건 쿼터 초과, 큐 적체). 완료조건: 데몬 다운 시 알림 1건 수신 검증.

### P1-4. 마이그레이션 번호 충돌 정리 · `db-infra/biop`
- `013_dart_employee_stats.sql` / `013_hiring_quarantine.sql` 중복 번호 → 신규는 타임스탬프 파일명 규칙(`migrate.py new`)으로 통일. 완료조건: 적용 이력 일관, .gitattributes로 SQL LF 강제(checksum 보호) 확인.

### P1-5. GPU 모델 검증 후 화이트리스트 편입 · `worker/biop`
- kronos/chronos2를 vast.ai GPU host에서 추론 검증 후 `ML_GATE_PASSED_MODELS` 편입 여부 결정. 완료조건: GPU 모델 가용성 게이트 통과·추론 정상 또는 "CPU 4종으로 출시" 명시 결정.

### P1-6. consensus_score UI 라벨·면책 문구 검수 · `web/seoeunjin` + `aggregator/jolly`
- consensus_score는 "신뢰도" 아닌 "소스 일치도"로 표기, 투자조언 금지 NOTICE·면책 문구(signal_core.safety) 모든 리포트 노출. 완료조건: 발행 리포트·소스 상세에 면책 문구 검증.

---

## P2 — 출시 후 개선 (백로그 적치)

- **Multi-source Aggregation 고도화** (`aggregator/jolly`): PRICE를 scoring source로 편입, source별 가중치, Alternative 직접쓰기 통합 — `docs/spec/final-signal-aggregator-spec.md §15`.
- **메타러너 재학습 자동화** (`worker/biop`): vol-benchmark walk-forward → `meta_learner.json` 산출 파이프라인화, 모델 버전 DB 테이블.
- **대시보드 완성** (`web/seoeunjin`): 구독자 대시보드 조회/분석 로직.
- **알림 시스템** (`main-server`): watchlist `notification_enabled` 실동작(가격/시그널 알림).
- **백테스팅 성과 검증** (`worker/biop`): `backtest_results` 활용 성과 리포트.
- **데이터 보관 정책**: analysis_results/ml_inferences/meta_signals 파티셔닝·로그 30일 보존, dependabot/SBOM.
- **patent/report 분석기 보강** (미배정 → 지정): report RAG 공용 인프라 정합화(과거 리뷰 지적), patent analyzer 완성.

---

## 권장 처리 순서 (의존성)

1. **P0-5/P0-6(인프라·시크릿) 착수** ─ 배포 타깃이 있어야 나머지를 실환경에서 검증 가능. 가장 먼저 시작.
2. **P0-1(OHLCV 백필) 병행** ─ 시간이 걸리는 데이터 작업이라 조기 시작.
3. **P0-4(로깅/Sentry)** ─ 이후 모든 E2E 검증의 관측 기반.
4. **P0-2(E2E 파이프라인) + P0-3(결제 실연동)** ─ 인프라·관측 위에서 실데이터 검증.
5. P1 → P2 순차.

---

## 검증 방법 (End-to-End)

배포 차단 항목이 실제로 풀렸는지 확인하는 절차:

1. **파이프라인 실데이터**: prod(또는 staging) DB에 OHLCV 백필 후
   `cd services/agent-worker && uv run pytest tests -q` (전체 green)
   → 실 DART_API_KEY로 e2e 트리거 → `final_signals`에 `is_published=true` row 생성 확인 → web `/report/[ticker]` 렌더 확인.
2. **ML 경로**: `ml_inferences`에 `pred_value NOT NULL`(폴백 아님), `meta_signals.method='stacking'` 확인. RISK_VETO 치명 키워드 종목 → `is_published=false`, validation_log 기록 확인.
3. **결제 real 모드**: 포트원 real 키로 본인인증→가입→9900 결제→구독활성→무제한 열람→취소 1회 통과.
4. **관측**: 의도적 5xx/데몬 다운 유발 → Sentry 이슈·구조화 로그·알림 수신 확인. `/health`가 DB 단절을 잡는지 확인.
5. **배포 자동화**: main 머지 → GitHub Actions가 이미지 빌드·배포 → 무중단(또는 짧은 다운) 반영, 롤백 1회 리허설.
6. **보안(P1)**: 로그인 brute force 시 rate limit 차단, 부정 Origin POST 거부 확인.

---

## 비고

- 보드 운영: CLOSED 킥오프 앵커는 보드에서만 제거(이슈 보존), 추론 배정 금지·라이브보드 직접수정 주의.
- DB: 새 마이그레이션은 정수 순번 폐기·타임스탬프 파일명. Alembic 재도입 금지(과거 백아웃). 런타임은 asyncpg raw.
- P0 항목은 추후 GitHub 이슈로 분해 가능(현재 단계에서는 문서로 공유).
