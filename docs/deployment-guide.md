# Signal Alpha 배포 가이드 (데모/포트폴리오)

> 이 문서는 졸업·포트폴리오 **데모(비상업)** 기준의 실행 가능한 배포 절차다.
> 상위 원칙은 [`deployment.md`](./deployment.md) 참고(서비스별 독립 배포).
> 비용 총합 ≈ **$5–20/월**.

## 0. 한눈에 — 무엇을 어디에 올리나

| 구성요소 | 정체 | 배포처 | 왜 |
| --- | --- | --- | --- |
| `web` | Next.js 프론트(현재 mock 데이터로 독립 동작) | **Vercel** (Hobby 무료) | 가볍고 Next.js 최적, PR 미리보기 배포 |
| `services/main-server` | 읽기 전용 API(FastAPI, 상시 구동) | **Railway** 컨테이너 | Vercel serverless에 부적합(상시 DB 풀) |
| `services/agent-worker` | 파이프라인 워커(FastAPI + 데몬) | **Railway** 컨테이너 | 큐 드레인·크롤링·임베딩 → 상시 컨테이너 필수 |
| DB | PostgreSQL | **Supabase**(서울, Free) | 이미 사용 중 |
| 파이프라인 스케줄 | 수집·정규화·분석 주기 구동 | **GitHub Actions cron** + Railway cron | "무엇이 주기적으로 굴리나"를 담당 |

> **용어**: *serverless*(Vercel) = 요청 올 때만 잠깐 켜졌다 꺼지는 실행 방식(시간·메모리 제한). *컨테이너*(Railway) = 항상 켜져 있는 작은 서버. 무겁거나 오래 도는 일은 컨테이너 몫.

```
[브라우저] ──https──> [Vercel: web] ──NEXT_PUBLIC_MAIN_API_BASE_URL──> [Railway: main-server :8000]
                                                                              │ 읽기
                                                                              v
                                                                     [Supabase Postgres]
                                                                              ^ 적재(final_signals 등)
                          [Railway: agent-worker :8011] ──큐 드레인/정규화/분석──┘
                                      ^
                          [GitHub Actions cron] ── 수집 CLI 실행 + 큐 드레인 트리거
```

핵심 분리: **web은 결과만 읽어 보여주고, 무거운 분석은 백그라운드가 미리 돌려 DB에 적재**한다
(`web/AGENTS.md` 원칙: 브라우저에서 scoring 중복 구현 금지, `final_signals` 기반 API를 표시 소스로).

---

## 1. 연결 — FE와 BE를 분리 배포하면 어떻게 이어지나

FE(Vercel)와 BE(Railway)는 **각자 다른 서버에 따로 배포되지만, 런타임에 HTTP로 연결**된다.
"하나로 합쳐 배포"하는 게 아니라 **서로의 주소를 알려주고, BE가 FE의 호출을 허용**하면 통합된다. 핵심 고리는 두 가지다.

### 1.1 주소 연결 — 환경변수 1개
- FE는 빌드/실행 시 `NEXT_PUBLIC_MAIN_API_BASE_URL` 값을 보고 그 주소로 API를 호출한다(`web/src/lib/apiClient.ts`).
- 그래서 **Vercel 환경변수에 BE(main-server)의 Railway 공개 URL을 넣어주면 연결 완료**다. 코드 수정 불필요, 값만 바꾸면 됨.
  - 로컬: `http://localhost:8000`
  - 데모: `https://<main-server>.up.railway.app`
- `NEXT_PUBLIC_` 접두사는 "이 값을 브라우저까지 노출한다"는 Next.js 규칙이라, **브라우저가 직접 BE를 호출**하는 구조다(서버 중계 없음).

### 1.2 허용 연결 — CORS
- 브라우저가 다른 도메인(Vercel→Railway)으로 요청하면, 받는 쪽(main-server)이 **"이 출처(origin)를 허용한다"**고 응답해야 브라우저가 막지 않는다(CORS).
- 따라서 main-server에 **Vercel 도메인을 허용 origin으로 등록**해야 한다. 빠지면 화면은 떠도 데이터 호출이 콘솔 에러로 막힌다.

### 1.3 데이터는 DB에서 만난다 (느슨한 결합)
- BE는 사용자 요청에 *즉석 계산*하지 않는다. 파이프라인(5장)이 **미리 분석해 `final_signals`를 Supabase에 적재**해 두고, main-server는 그걸 **읽어서** FE에 돌려준다.
- 즉 FE↔BE는 "API 호출"로, BE↔파이프라인은 "공유 DB"로 이어진다. 셋이 강하게 묶여 있지 않아 **독립 배포·독립 재시작**이 가능하다.

### 1.4 분리 배포라서 좋은 점
- FE만 고쳐도 BE 재배포 불필요(그 반대도 동일). Vercel은 **PR마다 미리보기 URL**을 자동 생성해 BE 없이도 UI 리뷰 가능.
- 장애 격리: 무거운 워커가 죽어도 FE/조회 API는 살아 있다(마지막 적재분을 계속 보여줌).

### 1.5 연결 순서 요약
```
1) BE(main-server) Railway 배포 → 공개 URL 확보
2) 그 URL을 Vercel 환경변수 NEXT_PUBLIC_MAIN_API_BASE_URL에 입력 → FE 재배포
3) main-server CORS 허용 origin에 Vercel 도메인 등록
4) 파이프라인(GitHub Actions)이 final_signals를 Supabase에 적재
→ 브라우저가 Vercel(FE)을 열면 Railway(BE) API를 호출하고, BE는 Supabase의 결과를 읽어 응답
```

---

## 2. web → Vercel

현재 `web`은 mock 데이터로 백엔드 없이도 뜬다 → **백엔드보다 먼저 데모 배포 가능**.

1. Vercel에서 New Project → 이 저장소 연결 → **Root Directory = `web/`** 지정(모노레포라 필수). 프레임워크는 Next.js 자동 인식.
2. **Environment Variables** 등록:
   - `NEXT_PUBLIC_MAIN_API_BASE_URL` = `https://<main-server>.up.railway.app` (4장 완료 후 채움)
   - 참조: `web/src/lib/apiClient.ts` — 이 값이 비어 있으면 `http://localhost:8000`로 폴백하고, 화면은 mock으로 렌더된다.
3. Hobby 플랜으로 Deploy → 공개 URL 확보. 이후 PR마다 preview 배포가 자동 생성되어 팀 UI 리뷰에 유용.

> ⚠️ Vercel Hobby는 **비상업 개인 프로젝트** 한정. 상업 전환 시 Pro($20/seat) 필요.

---

## 3. DB → Supabase (유지)

- 기존 prod(서울 `ap-northeast-2`) 그대로 사용. 신규 구축 불필요.
- **Free 주의점**: 7일간 쿼리가 전혀 없으면 프로젝트가 일시정지된다(데이터는 보존, 대시보드에서 수동 복원). 파이프라인 크론이 주기적으로 DB에 붙으면 사실상 멈추지 않는다.
- `DATABASE_URL`은 Supabase → Project Settings → Database → Connection string(`postgresql://...`)에서 복사. 모든 백엔드/CLI가 이 한 값을 공유한다.

---

## 4. main-server → Railway

> 팀 공용 인프라 영역. 아래는 설계/절차이며 실제 적용은 팀 결정.

1. Railway에서 New Service → 이 저장소 → **Dockerfile = `services/main-server/Dockerfile`**(완성됨, `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`).
2. **환경변수**: `DATABASE_URL`(3장) + 인증 시크릿. 참조: `services/main-server/app/core/config.py`.
3. **Public Domain** 생성 → 그 URL을 2장 Vercel의 `NEXT_PUBLIC_MAIN_API_BASE_URL`에 입력.
4. **CORS**: 브라우저가 main-server를 직접 호출하므로, Vercel 도메인 Origin을 허용하도록 설정(미설정 시 브라우저 콘솔에 CORS 차단 에러). → 1.2 참고.

---

## 5. agent-worker → Railway

> 팀 공용 인프라 영역. 설계/절차만 제시.

1. Railway New Service → **Dockerfile = `services/agent-worker/Dockerfile`**(`CMD uvicorn app.main:app --host 0.0.0.0 --port 8011`).
2. **메모리**:
   - alt-data(수집/정규화/분석)만 처리하면 **가볍게**(수백 MB) 떠도 된다.
   - **리포트 임베딩(EMBED_REPORT, BGE-M3 모델 ~2–3GB 상주)을 처리한다면 2–3GB+** 할당 필요. 참조: `services/agent-worker/app/embeddings/provider.py`.
3. **단일 인스턴스만** 운영. price_collector·ops_daemon이 PostgreSQL advisory lock으로 단일화를 가정하므로 스케일아웃(인스턴스 2개+) 금지.
4. **환경변수**: `DATABASE_URL`, `NAVER_CLIENT_ID/SECRET`, `KIPRIS_API_KEY`, `GEMINI_API_KEY`, `COLLECTOR_VERSION`(alt-data 필수) + 리포트/DART용 `DART_API_KEY`, `OPENAI_API_KEY`, AWS S3 자격증명. 참조: `services/agent-worker/app/core/config.py`.
5. **`/internal/*` 보안**: 이 서비스의 큐 제어 엔드포인트(`/internal/queue/*`, `/internal/schedules/*`)는 인증이 없다. **Public Domain을 붙이면 외부 노출**되므로 → (a) public domain 미부여(내부 네트워크 전용)하거나 (b) 토큰/프록시로 보호. 데모에선 (a)를 권장하고 파이프라인은 6장 cron으로 구동.

---

## 6. 파이프라인 오케스트레이션 — "무엇이 주기적으로 굴리나"

### 6.1 왜 별도 설계가 필요한가
agent-worker에는 **큐를 자동으로 비우는 상시 워커 루프가 없다**. 작업은 DB 큐(`processing_queue`)에 쌓이고,
누군가 주기적으로 (a) 수집을 트리거하고 (b) 큐를 드레인해야 흐른다. 자동으로 도는 것은 lifespan의
`price_collector`·`ops_daemon` 데몬뿐(가격 수집/운영 정리용, 큐 드레인 아님).

### 6.2 alt-data 전체 흐름 (우리 스코프)
```
run_collectors.py (CLI)            → raw_documents + processing_queue(NORMALIZE_PATENT/DATALAB)
   │  (hiring은 별도 수집 경로)
   v
NORMALIZE_* 드레인 (worker 큐 러너) → source_documents + signal_events  ┐ 정규화가 끝나면
                                                                      │ ANALYZE_ALTERNATIVE 자동 enqueue
   v                                                                  ┘
ANALYZE_ALTERNATIVE 드레인          → final_signals (소스별 1행)
   ※ run_analyzers.py (CLI)가 ANALYZE_ALTERNATIVE를 enqueue+드레인
```

**중요한 사실**: `run_collectors.py`·`run_analyzers.py`는 Supabase에 직접 붙는 CLI라 Actions 러너에서 그대로 실행된다.
**단, NORMALIZE_\* 큐 드레인은 독립 CLI가 없고 agent-worker 큐 러너로만 처리**된다. 따라서 정규화 단계는
agent-worker의 `POST /internal/queue/{task_type}/run-batch`를 호출하거나, agent-worker를 띄워 둔 상태여야 한다.

CLI 인자(확인됨):
- `python run_collectors.py [--patent-only|--datalab-only] [--ticker 005930] [--start-date YYYY-MM-DD] [--end-date ...]`
- `python run_analyzers.py [--hiring-only|--patent-only|--datalab-only] [--ticker 005930] [--since YYYY-MM-DD] [--limit N] [--loop --interval-seconds 3600]`

### 6.3 데모용 구동안 (권장)

**A. 수집 + 분석 트리거 = GitHub Actions cron** (무료, 기존 `datalab-daily.yml` 패턴 확장)
아래를 `.github/workflows/altdata-pipeline.yml`로 추가(예시). `workflow_dispatch`로 먼저 수동 검증한 뒤 `schedule`을 켠다.

```yaml
name: AltData Pipeline
on:
  workflow_dispatch:
  # 검증 전까지 schedule은 주석 유지 — prod Supabase에 자동 적재되므로 신중히.
  # schedule:
  #   - cron: "0 21 * * *"   # UTC 21:00 = KST 06:00
jobs:
  run:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: services/agent-worker
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      NAVER_CLIENT_ID: ${{ secrets.NAVER_CLIENT_ID }}
      NAVER_CLIENT_SECRET: ${{ secrets.NAVER_CLIENT_SECRET }}
      KIPRIS_API_KEY: ${{ secrets.KIPRIS_API_KEY }}
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
      COLLECTOR_VERSION: ${{ vars.COLLECTOR_VERSION }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      # 1) 수집 → NORMALIZE_* enqueue
      - run: uv run python run_collectors.py
      # 2) NORMALIZE_* 드레인: agent-worker run-batch 호출 (worker가 떠 있어야 함)
      #    worker 미배포 시 이 단계는 생략되고 정규화가 보류된다.
      - run: |
          for T in NORMALIZE_PATENT NORMALIZE_DATALAB NORMALIZE_HIRING ENRICH_PATENT; do
            curl -fsS -X POST "${{ secrets.WORKER_INTERNAL_URL }}/internal/queue/$T/run-batch" \
              -H "Content-Type: application/json" -d '{"max_runs": 50}' || true
          done
        if: ${{ secrets.WORKER_INTERNAL_URL != '' }}
      # 3) 분석 → final_signals
      - run: uv run python run_analyzers.py
```

> Secrets는 repo Settings → Secrets and variables → Actions에 등록(`DATABASE_URL`, `NAVER_*`, `KIPRIS_API_KEY`, `GEMINI_API_KEY`, 선택 `WORKER_INTERNAL_URL`). `COLLECTOR_VERSION`은 Variables로.

**B. 정규화 드레인 대안 = agent-worker 상시 + Railway cron**
agent-worker를 Railway에 띄우고, Railway의 Cron 서비스에서 위 2)의 `run-batch` 호출을 주기적으로 실행.
이러면 GitHub Actions는 1)·3)만, 정규화는 worker 쪽에서 처리된다.

### 6.4 리포트/DART 경로 (팀원 영역 — 설계만)
`COLLECT_REPORT → PROCESS_REPORT → NORMALIZE_REPORT → ANALYZE_REPORT`, `COLLECT_DART` 등은 Selenium·스토리지·선택적 LLM 보강이
필요해 **agent-worker(메모리 2–3GB)가 반드시 떠 있어야** 한다. Report RAG 임베딩(`EMBED_REPORT`)은 현재 런타임에 연결되어 있지 않다. 트리거는 `/internal/schedules/report/collect`,
`/internal/schedules/dart/collect`를 Railway cron 또는 Actions로 호출.
이 경로는 팀원 영역이므로 본 가이드는 설계 제시까지만 한다.

---

## 7. 비용 요약 (데모/비상업, 2026 기준)

| 대상 | 플랜 | 월 비용 |
| --- | --- | --- |
| Vercel (web) | Hobby (비상업) | $0 |
| Railway (main-server [+ agent-worker]) | Hobby $5 + 사용량 | ~$5–20 |
| Supabase (DB) | Free | $0 |
| GitHub Actions (파이프라인 cron) | 공개 repo 무료 분량 | $0 |
| **합계** | | **~$5–20/월** |

> alt-data만 시연하고 agent-worker를 가볍게(또는 미배포) 두면 Railway 비용이 하단($5)에 근접.
> 데이터가 커질 때 비용 최적화: 리포트 PDF 저장을 egress 무료인 Cloudflare R2로, 임베딩을 외부 API로 빼서 worker 메모리 절감.

---

## 8. 검증 (end-to-end)

1. **web 단독**: Vercel URL 접속 → 대시보드/드릴다운/백테스트/비교/저널 탭이 mock 데이터로 렌더되면 정상(백엔드 없이 동작).
2. **백엔드 헬스**: `curl https://<main-server>.up.railway.app/health` → 200. agent-worker 배포 시 `/health`도 확인.
3. **파이프라인(alt-data)**: Actions 워크플로를 `workflow_dispatch`로 수동 실행 → 로그에서 수집 건수와 `run_analyzers` SUMMARY(신호발행 수) 확인 → Supabase에서 `final_signals` 신규 row 생성 확인.
4. **연결(API 연동) 후**: Vercel `NEXT_PUBLIC_MAIN_API_BASE_URL`을 main-server 도메인으로 설정·재배포 → 브라우저 네트워크 탭에서 `/api/dashboard`·`/signals/{ticker}` 호출이 실제 응답(=DB의 final_signals 기반)으로 바뀌는지 확인.
5. **CORS**: 브라우저 콘솔에 CORS 차단 에러가 없는지 확인.

---

## 9. 범위·주의

- **우리(대체데이터 팀) 스코프** = `web`(2장) · alt-data 파이프라인 cron(6.3) · 본 문서. main-server·agent-worker 컨테이너 배포(4·5장)와 리포트/DART 경로(6.4)는 팀 공용 인프라/팀원 영역 → 설계·가이드 제공까지만, 실제 적용은 팀 결정.
- `agent-worker`에 public domain을 붙이면 `/internal/*` 보안(토큰) 필수.
- `web/src/*`의 미커밋 파일은 개인/동시세션 작업 — 배포 설정 외 코드는 건드리지 않는다.
