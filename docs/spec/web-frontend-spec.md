# Signal Alpha Web Frontend 스펙

> 기준일: 2026-06-23
> 대상: `web/` (Next.js 15 App Router)
> 목적: 데스크톱 시안(홈 `v2_converge` + 리포트 `v61`)을 디자인 베이스로, main-server(`:8000`) API와 1:1 연동되는 사용자-facing 웹 프론트엔드의 화면·상태·연동 계약을 고정한다.
> 연관 문서: 백엔드 [`main-server-api-spec.md`](./main-server-api-spec.md), 설계도 [`web/docs/frontend-architecture.md`](../../web/docs/frontend-architecture.md)

---

## 1. 범위와 원칙

Web Frontend는 브라우저에서 동작하는 사용자-facing UI다.

Frontend가 담당한다.

- 종목 검색 → 리포트 조회 흐름
- 회원가입/로그인/로그아웃, 토큰 보관과 자동 갱신
- 관심종목 등록/조회/삭제
- 구독 요금제 조회/변경/취소
- 관리자 로그인 및 회원/매출 대시보드
- 분석 진행 상태(스테퍼) 표시

Frontend가 하지 않는다.

- 데이터 수집/분석/스코어링(전부 백엔드)
- 비즈니스 규칙 판정(한도/권한은 백엔드가 최종 검증)
- 시세·재무 등 원천 데이터 직접 호출

모든 사용자-facing 문구는 투자 추천처럼 보이면 안 된다. 방향성은 "매수 우위 / 매도 우위 / 중립 / 혼조"로 표기하고, 모든 리포트에 `notice` 고지를 노출한다. [[main-server-api-spec]]의 원칙을 그대로 따른다.

---

## 2. 기술 스택 (확정)

| 항목 | 채택 | 비고 |
|---|---|---|
| 프레임워크 | Next.js 15 (App Router) | 실제 15.5.x |
| UI 라이브러리 | React 19 | |
| 언어 | TypeScript 5.7 | `npm run lint` = `tsc --noEmit` |
| 스타일 | Tailwind CSS v4 (`@theme`) + 일부 컴포넌트 클래스 | PostCSS 플러그인 `@tailwindcss/postcss` |
| 상태관리 | Zustand 5 | `authStore`/`watchlistStore`/`analysisStore` |
| 폰트 | Pretendard (CDN) | |
| 차트 | (없음) | 게이지/블롭은 커스텀 SVG·CSS. Recharts 제거됨 |
| 테스트 | `node --test` (스모크) | `web/tests/*.test.mjs` |

`NEXT_PUBLIC_MAIN_API_BASE_URL`로 백엔드 베이스 URL 주입(기본 `http://localhost:8000`).

---

## 3. 디자인 시스템 / 토큰

리포트 `v61`(navy/sky/green 미니멀)에서 확정. `web/src/app/globals.css`의 Tailwind v4 `@theme`에 1:1 매핑.

| 분류 | 토큰(`@theme`) | 값 |
|---|---|---|
| 배경 | `--color-bg` | `#FBFCFE` |
| 면 / 보조면 | `--color-surface` / `--color-surface-2` | `#FFFFFF` / `#F3F6FB` |
| 경계선 | `--color-line` | `#E7ECF3` |
| 본문/제목 | `--color-navy` / `--color-navy-soft` | `#0F1B33` / `#36425C` |
| 보조 텍스트 | `--color-muted` | `#8A97AB` |
| 강조(sky) | `--color-sky` / `--color-sky-deep` | `#0EA5E9` / `#0284C7` |
| 상승·긍정 / 하락·위험 | `--color-green` / `--color-red` | `#10B981` / `#EF4444` |
| 라운드 | `--radius-card` / `--radius-sm` | `18px` / `12px` |
| 그림자 | `--shadow-card` | `0 1px 2px rgba(15,27,51,.04),0 8px 24px rgba(15,27,51,.06)` |

규칙:

- 카드 = `surface` + `1px solid line` + `shadow` + `radius`(유틸 클래스 `.card`).
- 점수/BUY 강조 = sky→green 그라데이션(`.brand-grad`). 상승=green, 하락=red. **핑크/파스텔 금지**.
- 배경: 홈만 애니메이션(`.bg-fx` 컨버전스 파티클), 그 외 전 페이지는 정적 그라데이션(`.bg-static`).

---

## 4. 라우트 / 정보구조(IA)

`web/src/app` App Router 기준. 현재 구현된 라우트.

| 라우트 | 화면 | 인증 | 배경 |
|---|---|---|---|
| `/` | 홈(검색 hero + 타이핑 placeholder) | 공개 | 애니메이션 |
| `/report/[ticker]` | 리포트(v61 레이아웃) | 공개 조회 | 정적 |
| `/login`, `/signup` | 인증 폼(이메일/비번 + 소셜 자리표시) | 공개 | 정적 |
| `/mypage` | 관심종목 / 구독 / 회원정보 탭 | 인증 | 정적 |
| `/pricing` | 요금제(free/pro/premium) | 공개 | 정적 |
| `/admin` | 관리자 로그인 → 회원/매출 | 관리자 세션 | 정적 |

전역 레이아웃 `app/layout.tsx` → `AppShell`(상단 nav + 푸터). `AppShell`은 `flex flex-col` + `main flex-1`로 푸터를 뷰포트 하단에 고정한다.

---

## 5. 컴포넌트 아키텍처

```
AppShell                       상단 nav(홈/요금제/마이 + 로그인 토글) + 정적 배경 + 푸터
BackgroundFX                   홈 전용 컨버전스 파티클 캔버스(파티클→코어 트레일)
SearchHero                     홈 검색 hero + 타이핑 placeholder
PipelineStepper                분석 진행 스테이지(폴링 표시)
WatchlistButton                관심종목 추가/삭제 토글
AuthForm                       로그인/회원가입 공용 폼(소셜 버튼 자리표시)
report/ReportView              리포트 컨테이너(mast + 블롭 히어로 + 그리드 + prose)
report/FactorGrid              6타일(5팩터 chip+점수+dots + 핵심지표 타일)
report/RiskList                리스크 행(항목 · HIGH/MID/LOW)
```

파일 위치: `web/src/components/*`, 리포트 하위는 `web/src/components/report/*`.

---

## 6. 상태관리 (Zustand)

`web/src/stores/*`. 토큰 원본은 `web/src/lib/session.ts`가 소유(localStorage), 스토어/apiClient가 공유한다.

| 스토어 | 상태 | 주요 액션 |
|---|---|---|
| `authStore` | `user`, `status(idle/loading/authenticated/anonymous)`, `error` | `login`, `signup`, `logout`, `hydrate` |
| `watchlistStore` | `items`, `limit`, `count`, `loading` | `load`, `add`, `remove` |
| `analysisStore` | `ticker`, `status`, `polling` | `start`, `poll`, `reset` |

`hydrate()`는 앱 진입 시 저장된 access token으로 `GET /api/users/me`를 호출해 세션을 복원한다(`AppShell`에서 1회).

---

## 7. API 클라이언트 / 토큰 갱신

`web/src/lib/apiClient.ts`. 모든 호출은 fetch 래퍼를 거친다.

- 인증 모드: `user`(access token) / `admin`(관리자 세션 토큰) / `none`(공개).
- **토큰 갱신 인터셉터:** `user` 경로에서 401 수신 시 `POST /api/auth/refresh`로 1회 재발급 후 원요청 재시도. 실패하면 토큰 폐기.
- 에러는 `ApiError{status, code, message}`로 정규화. 백엔드의 `detail.{code,message}`를 그대로 매핑(예: `WATCHLIST_LIMIT_EXCEEDED`, `ADMIN_AUTH_REQUIRED`).
- 관리자 세션 토큰은 사용자 토큰과 **별도 키**(`sa_admin_session`)로 보관.

타입드 함수(일부): `signup/login/logout/getMe`, `searchStocks/listStocks`, `listWatchlists/addWatchlist/removeWatchlist`, `getSignalByTicker/getSignalDetail`, `getAnalysisStatus`, `listPlans/getMySubscription/changeSubscription`, `adminLogin/adminListUsers/adminGetStats`.

---

## 8. 데이터 변환 규칙 (백엔드 → 시안)

`web/src/lib/format.ts`.

| 백엔드 | 시안 표기 | 규칙 |
|---|---|---|
| `final_score` (0–100) | `7.5 / 10` | `round(score/10, 1)` |
| `alignment_rate` (0–1) | `82%` | `round(rate*100)` |
| `source_agreement` (HIGH/MEDIUM/LOW) | 높음/보통/낮음 | 라벨 매핑 |
| `direction` (POSITIVE/…) | 매수 우위/매도 우위/중립/혼조 | 투자 권유 표현 금지 |

**팩터 매핑(잠정, `FACTOR_MAP`):** 시안 5팩터 ↔ 백엔드 4소스. DART→재무·공시, PRICE→수급·시계열, REPORT→뉴스. 한 소스가 복수 팩터에 매핑되므로 같은 소스 팩터는 동일 값이 표시된다(설계 확정 전 한계).

리포트 raw 응답(`GET /signals/{ticker}`)의 `score_breakdown`·`caution_evidence`·`positive_evidence`는 JSONB가 **문자열**로 올 수 있어, 리포트 페이지가 `parseJson`으로 파싱한다.

---

## 9. 화면별 스펙

### 9.1 홈 `/`

- `SearchHero` + `BackgroundFX`(애니메이션).
- 검색창 placeholder는 고정 문구가 아니라 **실제 종목명**(`GET /api/stocks`)을 받아 타이핑되듯 흘려 보여 검색 가능 종목을 안내한다. 사용자가 입력을 시작하면 안내는 사라진다(placeholder 네이티브 동작).
- **검색 동작:** Enter(또는 분석 버튼) → `GET /api/stocks/search` → `pickBest`(코드 정확일치 > 종목명 정확일치 > 첫 결과)로 선택한 종목의 `/report/[code]`로 **즉시 라우팅**. 한글 IME 조합 중에도 첫 Enter로 검색되도록 `onKeyDown`으로 직접 처리.

### 9.2 리포트 `/report/[ticker]`

- `GET /signals/{ticker}`로 발행 시그널 조회.
- 발행 시그널이 없으면(404) "분석 준비 중" + `PipelineStepper`(`GET /api/analytics/{ticker}/status` 폴링) 표시.
- 발행 시그널이 있으면 `ReportView`(v61 레이아웃) 렌더:
  - **mast**: 종목명 + `코드 · 시장 · 섹터` + 관심종목 버튼.
  - **히어로**: 블롭 `AI Score(/10)` + `BUY · 매수 우위` 필 + thesis(`bull_point`).
  - **6타일**: 5팩터(chip ↗/→/↘ + 점수 + dots 5점) + 핵심 지표 타일(`score_breakdown.metrics`).
  - **prose**: 01 핵심 요약(`summary` + 태그 `positive_evidence`) / 02 리스크(`caution_evidence` HIGH/MID/LOW).
  - 하단 `notice` 고지 고정.

### 9.3 인증 `/login`, `/signup`

- `AuthForm` 공용. 이메일/비밀번호(8자 이상) + 회원가입 시 고지 동의 체크.
- 성공 시 토큰 저장 후 `/mypage`로 이동.
- **소셜 로그인(구글/네이버/카카오)은 자리표시 버튼만**(비활성). 별도 트랙(§12).

### 9.4 마이페이지 `/mypage`

- 인증 필요. 미인증이면 `/login`으로 리다이렉트.
- 탭: **관심종목**(`/api/watchlists` 목록/삭제), **구독**(`/api/subscriptions/me` + 변경/취소), **회원정보**(이메일/닉네임).

### 9.5 요금제 `/pricing`

- `GET /api/subscriptions/plans` 카드 렌더(free/pro/premium, 기능·가격).
- 구독 클릭 → 비로그인은 `/login`, 로그인은 `POST /api/subscriptions`(subscribe) 후 `/mypage`.

### 9.6 관리자 `/admin`

- 관리자 **세션 토큰** 방식(사용자 JWT와 분리).
- `POST /api/admin/login` → 세션 토큰 보관 → `GET /api/admin/stats`(MRR/회원/구독) + `GET /api/admin/users`(회원 테이블).

---

## 10. 인증 / 세션 처리

| 항목 | 처리 |
|---|---|
| 사용자 토큰 | access + refresh를 localStorage(`sa_access`/`sa_refresh`) |
| 자동 갱신 | 401 → refresh 1회 재시도(§7) |
| 세션 복원 | 진입 시 `hydrate()` → `GET /api/users/me` |
| 관리자 세션 | 별도 키 `sa_admin_session`, `Authorization: Bearer {session_token}` |
| 로그아웃 | `POST /api/auth/logout` 후 로컬 토큰 폐기(서버 실패와 무관하게 로컬 제거) |

---

## 11. 법적 고지

- 모든 리포트/시그널/구독 응답의 `notice`를 화면에 노출. 미존재 시 기본 문구로 폴백.
- direction 라벨은 데이터 방향성 톤. "사세요/파세요" 등 투자 권유 표현 금지.

---

## 12. 알려진 갭 / 별도 트랙

- **회원 테이블 재설계 + 소셜 로그인(구글/네이버/카카오)**: 사용자가 별도 설계/개발. 현재는 이메일/비번 인증 + 소셜 버튼 자리표시만.
- **관심종목 한도 불일치**: 프론트는 plan `max_watchlist`(free 3 / pro 20 / premium 100)를 표시하되, 백엔드 `WATCHLIST_LIMIT=10` 하드코딩과의 일원화는 백엔드 과제([[main-server-api-spec]] §7).
- **팩터 5 ↔ 소스 4 매핑**: `FACTOR_MAP` 잠정. 팀 확정 필요.
- **후속**: 실 PG(PortOne) 결제 연동, 분석 진행 SSE 실시간화(현재 폴링).

---

## 13. 빌드 / 테스트 / 실행

| 명령 | 용도 |
|---|---|
| `npm install` | 의존성 설치 |
| `npm run dev` | 개발 서버(:3000) |
| `npm run lint` | `tsc --noEmit` 타입체크 |
| `npm test` | `node --test` 스모크(`tests/*.test.mjs`) |
| `npm run build` | 프로덕션 빌드 |

주의:

- **dev 서버 가동 중 `npm run build` 금지** — 같은 `.next` 캐시를 덮어써 실행 중 dev 서버가 500(MODULE_NOT_FOUND)난다. 빌드 검증은 dev 중지 후 실행.
- 브라우저 연동에는 백엔드 CORS가 필요(`CORS_ALLOW_ORIGINS`, 기본 `http://localhost:3000`).
- 개발 인디케이터 배지는 `devIndicators:false`로 비활성(프로덕션 무관).

---

## 14. 프론트가 소비하는 백엔드 엔드포인트

| 화면/동작 | 엔드포인트 | 인증 |
|---|---|---|
| 회원가입/로그인/갱신/로그아웃 | `POST /api/auth/{signup,login,refresh,logout}` | 공개/refresh |
| 내 정보 | `GET /api/users/me` | 인증 |
| 종목 목록(placeholder) | `GET /api/stocks` | 공개 |
| 종목 검색 | `GET /api/stocks/search?query=` | 공개 |
| 관심종목 | `GET/POST /api/watchlists`, `DELETE /api/watchlists/{code}` | 인증 |
| 리포트(공개) | `GET /signals/{ticker}` | 공개 |
| 리포트 상세 | `GET /api/signals/{signal_id}` | 인증 |
| 분석 진행 | `GET /api/analytics/{ticker}/status` | 공개 |
| 구독 | `GET /api/subscriptions/{plans,me}`, `POST /api/subscriptions` | 공개/인증 |
| 관리자 | `POST /api/admin/login`, `GET /api/admin/{users,stats}` | 관리자 세션 |

신규 엔드포인트(`/api/stocks`, `/api/subscriptions/*`, `/api/admin/*`, `/api/analytics/*`)의 계약은 [[main-server-api-spec]] 및 설계도 §6과 일치한다.

---

## 15. 현재 구현 상태 (2026-06-23)

구현됨:

- 디자인 토큰(@theme), `AppShell`/`BackgroundFX`(홈 애니메이션·그 외 정적), 푸터 하단 고정.
- apiClient(토큰 갱신 인터셉터) + 3 Zustand 스토어 + 변환 유틸(`format.ts`).
- 페이지 7종(홈/리포트/로그인/회원가입/마이/요금제/관리자) — 전부 실 API 연동.
- 홈 타이핑 placeholder(실 종목명), Enter 즉시 리포트 이동, 한글 IME 단일 Enter 처리.
- 리포트 v61 레이아웃(mast·블롭 히어로·6타일·prose), 삼성전자(005930) 목업 시그널 1건 시드.
- `tsc` 통과, 스모크 테스트 green, `npm run build` 9라우트 green.

미구현/후속:

- 소셜 로그인 배선, 실 결제, SSE 실시간화, 시그널 상세(`/api/signals/{id}`) 전용 화면, 저널 화면.
