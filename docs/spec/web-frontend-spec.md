# Signal Alpha Web Frontend 스펙 (신규 기획)

> 기준일: 2026-06-24 (전면 재설계 — 포트원 본인인증/소셜연동/리포트 열람 쿼터/단일 구독)
> 대상: `web/` (Next.js 15 App Router + React 19 + Zustand 5 + Tailwind v4 + Recharts 3)
> 목적: 신규 기획에 맞춘 사용자-facing 웹의 화면·상태·API 소비 계약을 고정한다. 엔드포인트/응답 shape 정본은 백엔드 스펙이며 이 문서는 이를 1:1 참조한다.
> 연관 문서: [main-server-api-spec.md](./main-server-api-spec.md), [web-frontend-design.md](./web-frontend-design.md), [db-schema-spec.md](./db-schema-spec.md)

---

## 1. 범위와 원칙

Frontend가 담당한다: 종목 검색→리포트 흐름, 포트원 본인인증 가입/로그인, 소셜 연동/해제, 관심종목(무제한), 리포트 열람·쿼터 표시·비회원 블라인드, 소스 상세 5종, 저널, 구독 결제/취소, 관리자.

Frontend가 하지 않는다: 데이터 수집/분석/스코어링, 비즈니스 규칙 최종 판정(쿼터/권한은 백엔드가 최종 검증, 프론트는 표시·유도만).

모든 문구는 투자 추천처럼 보이면 안 된다. 방향성 라벨: `positive`→"상방 데이터 우세", `negative`→"하방 데이터 우세", `neutral`→"중립", `mixed`→"혼조", `unknown`→"데이터 없음". 모든 리포트에 `notice` 노출. [[main-server-api-spec]] 원칙 준수.

### 페이지 인벤토리

| 라우트 | 화면 | 인증 |
|---|---|---|
| `/` | 메인/검색 | 공개 |
| `/report/[ticker]` | 리포트(5소스 + 종합) | 공개(비회원 블라인드) |
| `/report/[ticker]/[source]` | 소스 상세 5종 | 공개(dart/datalab만)·회원 전체 |
| `/login` | 로그인(본인인증) | 공개 |
| `/signup` | 회원가입(본인인증) | 공개 |
| `/mypage` | 마이페이지(탭) | 회원 |
| `/pricing` | 구독/결제 | 공개 |
| `/admin` | 관리자 | 관리자 |

---

## 2. API 클라이언트 계약 (`web/src/lib/apiClient.ts`)

백엔드 응답 shape와 1:1 TS 타입. 베이스 URL `NEXT_PUBLIC_MAIN_API_BASE_URL`(기본 `http://localhost:8000`).

### 토큰/세션
- 저장: `localStorage` — `sa_access`, `sa_refresh`, 관리자 `sa_admin_session`.
- 401 → `POST /api/auth/refresh` 1회 재시도 → 실패 시 토큰 제거·로그아웃.
- 에러 정규화: `ApiError { status, code, message }` — `code` 는 백엔드 §2.4 레지스트리 공유.

### 엔드포인트 함수 (도메인별)

| 함수 | 메서드·경로 |
|---|---|
| `signup({imp_uid, nickname, agreed_risk, agreed_terms})` | POST `/api/auth/signup` |
| `login({imp_uid})` | POST `/api/auth/login` |
| `refresh()` / `logout()` | POST `/api/auth/refresh` / `/api/auth/logout` |
| `getMe()` / `updateMe({nickname})` / `deleteMe()` | GET/PATCH/DELETE `/api/users/me` |
| `listSocial()` / `linkSocial(provider, {code,redirect_uri})` / `socialLogin(provider,...)` / `unlinkSocial(provider)` | `/api/auth/social/*` |
| `searchStocks(query)` / `listStocks()` | GET `/api/stocks/search` / `/api/stocks` |
| `getWatchlist()` / `addWatchlist(stock_code)` / `removeWatchlist(stock_code)` | `/api/watchlists*` |
| `getReport(stock_code)` | GET `/api/reports/{stock_code}` |
| `issueReport(stock_code)` | POST `/api/reports/{stock_code}/issue` |
| `getQuota()` | GET `/api/reports/quota` |
| `getSourceDetail(stock_code, source)` | GET `/api/reports/{stock_code}/sources/{source}` |
| `listJournals(params)` / `createJournal(body)` / `get/patch/deleteJournal(id)` | `/api/journals*` |
| `getPlans()` / `getMySubscription()` | `/api/subscriptions/*` |
| `checkout()` / `confirmPayment({imp_uid, merchant_uid})` / `cancelPayment()` | `/api/payments/*` |
| `adminLogin()` / `adminLogout()` / `adminListUsers(params)` / `adminGetUser(id)` / `adminSetSubscription(...)` / `adminStats()` | `/api/admin/*` |

### 핵심 타입 (발췌)
```ts
type ReportSource = { source: 'price'|'dart'|'hiring'|'datalab'|'report'; direction: string|null; score: number|null; data_status?: string; summary: string|null; locked: boolean };
type Report = {
  stock: Stock;
  report_version?: { final_signal_id: number; run_key: string; signal_date: string; updated_at: string };
  direction: string|null; score: number|null; alignment_rate: number|null;
  source_agreement?: string; warning_level?: string; data_status?: string; summary: string|null;
  sources: ReportSource[];
  access: { unlocked: boolean; is_member: boolean; issued_via?: 'free'|'subscription'; free_remaining?: number };
  notice: string;
};
type Quota = { free_quota: number; free_used: number; free_remaining: number; subscription_active: boolean };
type SocialLink = { provider: 'naver'|'google'|'kakao'; linked: boolean; linked_at?: string };
```

### 변환 유틸 (`web/src/lib/format.ts`)
`directionLabel`(소문자 enum→한글), `scoreOutOf10`(0–100→/10), `alignmentPercent`(0–1→%), `agreementLabel`(HIGH/MEDIUM/LOW→높음/보통/낮음), `SOURCE_LABEL`(price→"주식정보", dart→"DART", hiring→"채용공고", datalab→"네이버 키워드", report→"증권사 리포트"), `won`(통화).

---

## 3. 상태 관리 (Zustand stores, `web/src/stores/*`)

| store | 상태/액션 |
|---|---|
| `authStore` | `user`, `status`(idle/loading/authenticated/anonymous), `loginWithImpUid`, `signup`, `logout`, `hydrate` |
| `socialStore` | `links[]`, `load`, `link(provider)`, `unlink(provider)` |
| `watchlistStore` | `items`, `count`(무제한, limit 없음), `load`, `add`, `remove` |
| `reportStore` | `report`, `loading`, `load(stock)`, `issue(stock)`(언락) |
| `quotaStore` | `free_remaining`, `subscription_active`, `load` |
| `paymentStore` | `checkout`, `confirm`, `cancel`, `status` |
| `journalStore` | `items`, `load`, `create`, `update`, `remove` |
| `adminStore` | `session`, `login`, `logout`, `users`, `stats`, `setSubscription` |
| `toastStore` | `toasts`, `show(message, tone)`, `dismiss` |

---

## 4. 인증 플로우 (포트원 본인인증)

- `/signup` 과 `/login` 은 **버튼·화면 분리**. 아이디/비밀번호 입력란 **없음**.
- 포트원 본인인증 위젯(`IMP.certification`) 호출 → `imp_uid` 획득 → 백엔드 `signup`/`login` 호출.
- 가입 화면: 위험 고지/약관 동의 체크(`agreed_risk` 필수, 미동의 시 가입 버튼 비활성), 닉네임 선택.
- 미가입 상태로 `/login` 본인인증 시 `404 USER_NOT_FOUND` → "가입이 필요합니다" 토스트 + `/signup` 유도.
- 포트원 SDK: `NEXT_PUBLIC_PORTONE_IMP_CODE` 사용. 위젯 스크립트는 인증/결제 페이지에서 로드.

---

## 5. 소셜 로그인 연동 UI

- **연동**은 로그인 상태에서만(마이페이지 탭). 외부 진입 소셜 로그인은 `socialLogin` → 미연동이면 안내("연동된 계정만 간편 로그인 가능, 먼저 본인인증 가입").
- 마이페이지 소셜 탭: provider별 토글(연동/해제). 해제 시 확인 모달 → `unlinkSocial`.
- 로그아웃: 서비스 세션 폐기 + 연동 provider 사별 로그아웃 처리(백엔드 위임, 프론트는 결과 반영).
- `web/src/components/AuthForm.tsx` 의 비활성 소셜 버튼 → 실연동 동작으로 교체.

---

## 6. 메인/검색 (`/`)

검색 히어로(애니메이션 배경 `bg-fx`) → 입력 → `searchStocks` 자동완성 → 선택 시 `/report/{stock_code}` 이동. 비로그인도 가능(리포트에서 블라인드).

---

## 7. 리포트 (`/report/[ticker]`)

- `getReport(ticker)` 로 현재 버전 로드. 5개 소스 카드(주식정보/DART/채용공고/네이버 키워드/증권사 리포트) 각 LLM 요약 + 종합 게이지(`score`/`direction`/`alignment_rate`).
- 각 카드 클릭 → `/report/[ticker]/[source]`.
- **열람/쿼터 UI**:
  - 회원·미언락: "리포트 발행(열람)" 버튼 → `issueReport`. 성공 시 전체 표시 + `free_remaining` 배지.
  - `402 REPORT_QUOTA_EXCEEDED` → 구독 유도 모달(`/pricing`).
  - 구독자: 무제한, 쿼터 배지 숨김 또는 "구독 중".
  - 새 버전 안내: `report_version.updated_at` 변동 시 "업데이트된 리포트가 있습니다" → 재발행(차감).
- **비회원 블라인드**: `access.unlocked=false` → DART·네이버 카드만 노출, 나머지(주식정보/채용/증권사리포트)와 종합점수는 잠금 오버레이 + 로그인/가입 CTA.

---

## 8. 소스 상세 5종 (`/report/[ticker]/[source]`)

`source ∈ price|dart|hiring|datalab|report`. `getSourceDetail` → 원천 데이터 테이블/차트(Recharts) + LLM 상세 요약.
- price: 시세·재무 지표(PER/PBR/ROE) 차트.
- dart: 공시 목록(제목/유형/날짜/원문 링크).
- hiring: 공고수·증감 추이.
- datalab: 검색지수·급등 표시.
- report: 증권사/목표가/투자의견.
- 접근: 비회원은 dart/datalab만(나머지 `401 MEMBERSHIP_REQUIRED` → 잠금 화면). 회원 미언락은 dart/datalab 외 블라인드.

---

## 9. 마이페이지 (`/mypage`)

탭 구성:
1. **회원정보/수정/탈퇴**: `getMe`/`updateMe`(닉네임)/`deleteMe`(탈퇴 확인 모달).
2. **관심종목**: `getWatchlist`(무제한 목록) + `addWatchlist`/`removeWatchlist`.
3. **구독**: `getMySubscription`(현황) + 결제(`/pricing` 또는 인라인) + `cancelPayment`(취소).
4. **저널**: 저장한 리포트 추이.
5. **소셜 연동/해제**: §5.

---

## 10. 저널 UI

**전체 구독 전용** — 모든 저널 API 가 402 `SUBSCRIPTION_REQUIRED` 를 던지므로, 비구독자는 마이페이지 저널 탭에서 구독 유도 패널(`data-flow="journal-subscribe"` → `/pricing`)을 본다.

- 저장 진입점: 리포트 페이지(`/report/[ticker]`) 하단, 구독자 언락 시 `data-flow="journal-save"` — `journalStore.create({stock_code, final_signal_id, user_view, memo, tags})`.
- 마이페이지 저널 탭: `journalStore`(items/load/create/update/remove) 기반 목록 + 카드별 수정(`journal-edit`, user_view 3버튼+memo+tags)·삭제(`journal-delete`)·태그 pill.
- 추이 표시: 카드에 저장 시점 스냅샷(`signal_score_at_time`·`signal_value_at_time`)과 outcome 확정 결과("7거래일 후 +x% · 30거래일 후 −y%", 미확정 시 "변동 확정 전").
- 주가 차트: 저널 카드 클릭 → `getJournalChart(id)` 로 SVG 라인차트 펼침(`data-flow="journal-chart"`, `<JournalChartPanel/>`). 작성 시점 기준선(수직 날짜 + 수평 가격 점선)과 "작성 시점 대비 ▲/▼ ±x%" 헤더, 등락 색(상승 red/하락 sky — 부호 텍스트 병기로 색 단독 인코딩 금지), 호버 툴팁·데이터 표 포함. 동기화 전엔 "차트 준비 전".
- `user_view` = 계속 관찰/추가 확인 필요/낮은 관련도. 매수·매도 표현 금지.

---

## 11. 가격/구독·결제 (`/pricing`)

- `getPlans` → 단일 상품 9,900원 카드(무제한 열람 강조). 비회원·무료 회원과 비교.
- 결제: `checkout` → 포트원 결제창(`IMP.request_pay`, `pg: html5_inicis`) → 성공 콜백 `imp_uid`/`merchant_uid` → `confirmPayment` 서버 검증 → 구독 활성 반영.
- 취소: 마이페이지 구독 탭에서 `cancelPayment` → 취소 후에도 무료 잔여분 사용 가능 안내.

---

## 12. 관리자 (`/admin`)

- `adminLogin`(하드코딩 계정) → `sa_admin_session`. `adminLogout`.
- 대시보드: `adminStats`(총매출/MRR/구독자수, Recharts) + `adminListUsers`(목록·검색·페이지네이션) + 회원 상세 → 구독 등록/수정/취소(`adminSetSubscription`).
- 일반 사용자 인증과 분리된 세션 헤더.

---

## 13. 공통 컴포넌트

- **잠금 오버레이**(`<LockedOverlay/>`): 비회원/미언락 소스·종합점수 위 블러 + CTA.
- **쿼터 배지**(`<QuotaBadge/>`): "무료 N회 남음" / "구독 중".
- **쿼터 소진 모달**: `402` 시 구독 유도.
- **notice 토스트/푸터**: 모든 리포트 응답 `notice` 노출.
- 디자인 토큰·패턴은 [web-frontend-design.md](./web-frontend-design.md).

---

## 14. 프론트 정합화 과제 (현 코드 → 목표)

- **교체**: `authStore`/`AuthForm`/`/login`/`/signup` 의 이메일·비번 → 포트원 본인인증. `watchlistStore` 의 `limit` 제거(무제한). `apiClient.ts` 의 `/api/signals*` 소비 → `/api/reports/*`.
- **신규**: `reportStore`/`quotaStore`/`socialStore`/`paymentStore`/`journalStore`/`adminStore`. 소스 상세 라우트 `[source]`, 마이페이지 탭(탈퇴/소셜/구독), 잠금 오버레이·쿼터 배지 컴포넌트, 포트원 SDK(인증/결제) 로딩.
- **표준화**: `direction` 소문자 enum 기준 라벨 변환, `SOURCE_LABEL` 5종, 모든 리포트 `notice` 노출.

---

## 15. 페이지 ↔ 엔드포인트 매트릭스 (검증용)

| 페이지 | 호출 엔드포인트 |
|---|---|
| `/` | `GET /api/stocks/search` |
| `/report/[ticker]` | `GET /api/reports/{code}`, `POST /api/reports/{code}/issue`, `GET /api/reports/quota` |
| `/report/[ticker]/[source]` | `GET /api/reports/{code}/sources/{source}` |
| `/login` `/signup` | `POST /api/auth/login` `signup`, 포트원 본인인증 |
| `/mypage` | `GET/PATCH/DELETE /api/users/me`, `/api/watchlists*`, `/api/subscriptions/me`, `/api/payments/cancel`, `/api/journals*`, `/api/auth/social*` |
| `/pricing` | `GET /api/subscriptions/plans`, `/api/payments/checkout`·`confirm` |
| `/admin` | `/api/admin/*` |
