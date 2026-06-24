# Signal α 프론트엔드 설계도 (frontend-architecture)

> 본 문서는 데스크톱 시안(홈 `v2_converge` + 리포트 `v61` 재채색)을 디자인 베이스로,
> 실제 main-server(`:8000`) API와 1:1 연동되는 web(Next 15) 프론트엔드의 설계 계약이다.
> 코드 스캐폴딩(Phase 4)과 신규 백엔드 엔드포인트(Phase 3)는 이 문서를 단일 출처로 삼는다.

---

## 1. 디자인 시스템 / 토큰

리포트 `v61`(navy/sky/green 미니멀)에서 확정한 토큰. Tailwind v4 `@theme`(globals.css)에 1:1 매핑한다.

| 분류 | 토큰 | 값 | Tailwind `@theme` 변수 |
|---|---|---|---|
| 배경 | `--bg` | `#FBFCFE` | `--color-bg` |
| 면 | `--surface` | `#FFFFFF` | `--color-surface` |
| 면(보조) | `--surface-2` | `#F3F6FB` | `--color-surface-2` |
| 경계선 | `--line` | `#E7ECF3` | `--color-line` |
| 본문/제목 | `--navy` | `#0F1B33` | `--color-navy` |
| 본문(부드러움) | `--navy-soft` | `#36425C` | `--color-navy-soft` |
| 보조 텍스트 | `--muted` | `#8A97AB` | `--color-muted` |
| 강조(sky) | `--sky` | `#0EA5E9` | `--color-sky` |
| 강조(sky deep) | `--sky-deep` | `#0284C7` | `--color-sky-deep` |
| 상승/긍정 | `--green` | `#10B981` | `--color-green` |
| 하락/위험 | `--red` | `#EF4444` | `--color-red` |
| 브랜드 그라데이션 | `--grad` | `linear-gradient(135deg,#0EA5E9,#10B981)` | 유틸 `.bg-brand-grad` |
| 라운드 | `--radius` / `--radius-sm` | `18px` / `12px` | `--radius-card` / `--radius-sm` |
| 그림자 | `--shadow` | `0 1px 2px rgba(15,27,51,.04),0 8px 24px rgba(15,27,51,.06)` | `--shadow-card` |
| 폰트 | `--sans` | `"Pretendard Variable",Pretendard,sans-serif` | `--font-sans` |
| 모션 ease | `--ease` | `cubic-bezier(.22,.61,.36,1)` | `--ease-out` |

**카드 규칙:** `background:surface` + `1px solid line` + `shadow` + `radius`. (clay 다중 인셋 섀도 폐기.)
**점수/BUY 강조:** sky→green 그라데이션. **상승=green, 하락=red.** 핑크/파스텔 사용 금지.

---

## 2. 정보구조(IA) · 라우트 맵

| 라우트 | 화면 | 인증 | 비고 |
|---|---|---|---|
| `/` | 홈(검색 hero + converge 배경) → 검색·스테퍼·리포트 3-스테이지 | 공개 | `v2_converge` |
| `/report/[ticker]` | 리포트(v61 레이아웃) | 공개(상세 일부 인증) | `GET /signals/{ticker}` |
| `/analyze/[ticker]` | 분석 진행(라이브 파이프라인 스테퍼) | 공개 | 또는 `/` 내 스테이지 전환 |
| `/login`, `/signup` | 인증 폼(이메일/비번 + **소셜 버튼 자리표시**) | 공개 | |
| `/mypage` | 관심종목 / 회원정보 / 구독 탭 | 인증 | |
| `/pricing` | 요금제(free/pro/premium) + 결제 진입 | 공개 | |
| `/admin` | 회원 / 구독 / 매출 탭 | **관리자 세션** | `get_current_admin` |

기존 `web/src/app`에는 dashboard `page.tsx`만 존재 → 위 라우트 구조로 재구축.

---

## 3. 컴포넌트 아키텍처

```
AppShell (Nav + 로그인 토글 + 본문 슬롯)
├─ SearchHero            홈 검색 hero + converge 캔버스 배경
├─ PipelineStepper       분석 진행 스테이지(폴링 → 단계 표시)
├─ ReportView           리포트 컨테이너(v61)
│   ├─ ScoreGauge        AI Score 블롭/게이지 (score/10)
│   ├─ FactorGrid        6-타일 팩터 그리드(chip ↗/→/↘ + dots 평점)
│   ├─ SourceSection     소스별 패널(요약·근거칩·태그)
│   ├─ RiskList          리스크 HIGH/MID/LOW
│   └─ EvidenceChips     근거 문서/이벤트 칩
├─ WatchlistButton       관심종목 추가/삭제 토글
├─ AuthForms             로그인/회원가입 폼
└─ AdminTable            관리자 회원/구독 테이블
```

재사용: 기존 `web/src/components/AppShell.tsx`를 시안 nav로 재작성. 게이지는 v61의 SVG/blob 마크업 이식(Recharts 불필요, 매출 차트만 Recharts 사용).

---

## 4. 상태관리 (Zustand 5)

| 스토어 | 상태 | 액션 |
|---|---|---|
| `authStore` | `accessToken`, `refreshToken`, `user`, `isAdmin` | `login`, `logout`, `refresh`, `loadMe` |
| `analysisStore` | `ticker`, `stage`, `tasks[]`, `polling` | `start`, `poll`, `reset` |
| `watchlistStore` | `items[]`, `limit`, `count` | `load`, `add`, `remove` |

**토큰 갱신 인터셉터 규약:** `apiClient` fetch 래퍼에서 401(`TOKEN_EXPIRED`) 수신 시 `POST /api/auth/refresh`로 1회 재발급 → 원요청 재시도. 실패 시 `logout()`. 토큰은 메모리 + (선택)`localStorage` 보관. 관리자 세션 토큰은 별도 키로 분리.

---

## 5. API 연동 매핑 (기존 엔드포인트 — main-server `:8000`)

현재 등록 라우터 8종(`health/auth/users/stocks/watchlists/dashboard/signals/journals`) 기준.

| 화면/동작 | 엔드포인트 | 인증 | 응답 핵심 필드 |
|---|---|---|---|
| 회원가입/로그인 | `POST /api/auth/{signup,login}` | 공개 | `user`, `access_token`, `refresh_token`, `notice` |
| 토큰 갱신/로그아웃 | `POST /api/auth/{refresh,logout}` | refresh | `access_token`… / `{status}` |
| 내 정보 | `GET /api/users/me` | 인증 | `id,email,nickname,agreed_risk,is_verified` |
| 종목 검색 | `GET /api/stocks/search?query=&limit=` | 공개 | `items[]{id,stock_code,stock_name,market,sector}` |
| 관심종목 목록/추가/삭제 | `GET/POST /api/watchlists`, `DELETE /api/watchlists/{stock_code}` | 인증 | `limit,count,items[]{stock,notification_enabled,created_at}` |
| 대시보드 | `GET /api/dashboard` | 인증 | `user,watchlist_limit,watchlist_count,items[]{stock,latest_signal,source_summary,journal}` |
| 리포트(공개 단건) | `GET /signals/{ticker}` | 공개 | raw `final_signals` row |
| 리포트(종목 코드) | `GET /api/signals/by-stock/{stock_code}` | 인증 | `signal_id,stock,direction,score,alignment_rate,source_agreement,warning_level,summary` |
| 리포트(상세) | `GET /api/signals/{signal_id}` | 인증 | `score,direction,source_agreement,positive_evidence,caution_evidence,sources[]{source,direction,score,evidence[]},notice` |
| 시그널 읽음 | `POST /api/signals/{signal_id}/read` | 인증 | `status,read_at` |
| 시그널 목록 | `GET /api/signals?stock_ids=1,2` | 인증 | `[]{stock,direction,score,alignment_rate,source_agreement,warning_level,score_breakdown}` |
| 저널 | `/api/journals*` (journals_router) | 인증 | (저널 MVP) |

### 5-1. 데이터 변환 규칙 (백엔드 → 시안)

| 백엔드 | 시안 표기 | 규칙 |
|---|---|---|
| `final_score` (0–100) | `7.5 / 10` | `round(final_score/10, 1)` |
| `alignment_rate` (0–1, `consensus/100`) | `82%` | `round(alignment_rate*100)` |
| `source_agreement` `HIGH/MEDIUM/LOW` | 신뢰도 `높음/보통/낮음` | 라벨 매핑 |
| `direction` `POSITIVE/NEGATIVE/NEUTRAL/MIXED` | `매수 우위/매도 우위/중립/혼조` | **투자 권유 표현 금지**(BUY 칩은 "매수 우위" 톤) |
| `warning_level` `WARNING/CAUTION/NORMAL`, `data_status` | 데이터 품질 배지 | `failed/partial/ok` |

### 5-2. 팩터 매핑 (시안 6타일 ↔ 백엔드 `score_breakdown` 4소스) — **잠정, 팀 확정 필요**

백엔드 소스는 4종(`DART/PRICE/REPORT/ALTERNATIVE`), `ALTERNATIVE`는 하위 `hiring/patent/datalab`. 시안은 5개 팩터 + 핵심지표 타일. 불일치 구간을 아래로 정의(한 소스가 복수 팩터에 매핑됨):

| 시안 팩터 | 백엔드 소스 | 근거 |
|---|---|---|
| 재무 건전성 | `DART` (재무 facts) | `dart_financial_facts` |
| 공시 이벤트 | `DART` (signal_events) | 공시 이벤트 |
| 수급 모멘텀 | `PRICE` | 투자자별 매매/수급 |
| 시계열 추세 | `PRICE` (일봉 추세) | 같은 소스의 시계열 측면 |
| 뉴스 감성 | `REPORT` + `ALTERNATIVE` 보조 | 증권사 리포트 RAG + 대체데이터 |
| 핵심 지표(PER/PBR/영업이익률) | 비점수 메타 | 표시용 |

> ⚠️ DART→2팩터, PRICE→2팩터로 분기. 팀에서 "팩터=소스 1:1"로 정리할지, 시안의 5팩터를 유지할지 확정 필요. 미확정 동안 프론트는 위 매핑 상수표(`lib/factorMap.ts`)로 처리.

---

## 6. 신규 백엔드 API 계약 (Phase 3에서 실제 구현) — **확정**

기존 라우터 패턴(`auth.py`): `APIRouter(prefix)`, `Depends(get_database_pool/get_settings)`, inline Pydantic, `_api_error({code,message})`, 응답 `notice`. 스키마는 `001_baseline.sql` 기존 컬럼 사용(신규 마이그레이션 지양).

### 6-1. 구독 (`subscriptions_router`, prefix `/api/subscriptions`)

```
GET  /api/subscriptions/plans          # 공개. subscription_plans(is_active) 목록
  → { plans: [ { plan_type, plan_display_name, max_watchlist, signal_delay_hours,
                 journal_max_entries, has_alt_data, has_detail_report, has_backtesting,
                 price_monthly, price_yearly } ] }    # 시드: free/pro/premium

GET  /api/subscriptions/me             # 인증. 현재 활성 구독(없으면 free 간주)
  → { subscription: { plan_type, status, started_at, expires_at, billing_cycle } | null,
      plan: <plans 항목>, notice }

POST /api/subscriptions                # 인증. 변경/취소
  body: { plan_type: "pro"|"premium"|"free", billing_cycle?: "monthly"|"yearly",
          action: "subscribe"|"cancel" }
  → { subscription, plan, notice }
```
리포지토리 추가(`users_billing.py`): `list_subscription_plans()`, `get_subscription_by_user(user_id)`, `cancel_subscription(user_id)`. 활성 구독은 부분 유니크 인덱스(`idx_subscription_active`)로 user당 1개. 결제는 `record_portone_verification`(기존) 연계, 실 PG는 후속.

### 6-2. 관리자 (`admin_router`, prefix `/api/admin`)

```
POST /api/admin/login                  # 이메일/비번 → admin_sessions 세션 토큰 발급
  → { session_token, expires_at }
GET  /api/admin/users?page=&size=&q=   # 관리자. 페이지네이션
  → { total, page, size, items: [ { id, email, nickname, created_at,
        subscription: { plan_type, status } | null } ] }
GET  /api/admin/users/{id}             # 관리자. 단건 상세(+구독/관심종목 수)
PUT  /api/admin/users/{id}/subscription# 관리자. 구독 등급 수동 변경
  body: { plan_type, status?, expires_at? }
GET  /api/admin/stats                  # 관리자. 매출/구독 집계
  → { mrr, total_users, active_subscriptions, by_plan: { free, pro, premium },
      revenue_monthly: [ { month, amount } ] }
```
**인가 의존성 `get_current_admin`(신규):** `auth.py:get_current_user` 미러링, Bearer **세션 토큰** → `admin_sessions`(만료/활성 검증) → `AdminRepository.get_admin_by_id`. 리포지토리 추가: `get_admin_by_id`, `list_users_paginated`, `get_user_details`, 집계 쿼리. 모든 `/api/admin/*`(login 제외) `Depends(get_current_admin)`.

### 6-3. 분석 진행 (`analytics_router`, prefix `/api/analytics`)

```
GET  /api/analytics/{ticker}/status    # 폴링. processing_queue 상태 → 스테퍼 단계
  → { ticker, overall: "pending"|"running"|"success"|"failed",
      stages: [ { task_type, status, updated_at } ], notice }
```
`ProcessingQueueRepository.list_tasks`(기존)로 상태 집계 → 스테퍼 매핑. 트리거가 필요하면 `app/clients/agent_client.py`(신규)로 agent-worker `/internal/tasks/{task_type}/enqueue` 호출. (현재 폴링, SSE 실시간화는 후속.)

---

## 7. 법적 고지 (필수)

- 모든 리포트/시그널/구독 응답의 `notice` 필드를 화면에 노출. 상수:
  `"Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."`
- direction 라벨은 "매수 우위/매도 우위" 등 **데이터 방향성** 톤. "사세요/파세요" 등 투자 권유 표현 금지.
- 리포트 하단 disclaimer 고정 노출.

---

## 8. 알려진 갭 / 별도 트랙

- **관심종목 한도 불일치:** `watchlists.py`는 `WATCHLIST_LIMIT=10` 하드코딩인데 `subscription_plans.max_watchlist`는 free 3 / pro 20 / premium 100. 프론트는 구독 plan의 `max_watchlist`를 표시하되, **백엔드 한도 일원화는 Phase 3에서 plan 기준으로 정렬** 필요(별도 결정).
- **별도 트랙(본 설계 범위 외):** 회원 테이블 재설계 + **소셜 로그인(구글/네이버/카카오)** 및 그와 묶인 상세 구독 등급 설계는 사용자가 별도로 진행. 본 설계는 기존 이메일/비번 인증을 사용하고, `/login`·`/signup`에 소셜 버튼 **자리표시(placeholder)만** 둔다.
- **후속:** 실 PG(PortOne) 결제 연동, 분석 진행 SSE 실시간화.

---

## 부록 — 기존/신규 엔드포인트 요약

- 기존(구현됨): `health`, `auth(signup/login/refresh/logout)`, `users/me`, `stocks/search`, `watchlists(GET/POST/DELETE)`, `dashboard`, `signals(/{ticker}, by-stock, /{id}, /{id}/read, list)`, `journals`.
- 신규(Phase 3): `subscriptions(plans/me/POST)`, `admin(login/users/users/{id}/users/{id}/subscription/stats)`, `analytics/{ticker}/status`.
