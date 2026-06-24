# Signal Alpha Main Server API 스펙 (신규 기획)

> 기준일: 2026-06-24 (전면 재설계 — 포트원 본인인증/소셜연동/리포트 열람 쿼터/단일 구독/관리자)
> 대상: `services/main-server`
> 목적: 신규 제품 기획에 맞춘 사용자-facing API 계약을 확정한다. 프론트(생산자=백엔드, 소비자=웹)가 이 문서를 정본으로 참조한다.
> 연관 문서: [db-schema-spec.md](./db-schema-spec.md), [web-frontend-spec.md](./web-frontend-spec.md), [web-frontend-design.md](./web-frontend-design.md), [final-signal-aggregator-spec.md](./final-signal-aggregator-spec.md), [source-agent-contract.md](./source-agent-contract.md)

---

## 1. 범위와 원칙

Main Server는 웹/외부 클라이언트가 호출하는 사용자-facing API 경계다.

**담당한다**: 본인인증 기반 회원가입/로그인, 소셜 연동/해제, 회원정보·수정·탈퇴, 관심종목, 리포트 열람(쿼터)·소스 상세, 저널, 결제(구독)·취소, 관리자.

**하지 않는다**: 외부 데이터 수집, 소스 분석, LLM/RAG 호출, agent-worker 내부 큐 처리, 최종 시그널 생성. 리포트 본문(`final_signals`)은 agent-worker가 사전 생성하며 Main Server는 **저장본을 읽어** 제공한다.

모든 사용자-facing 문구·필드는 투자 추천처럼 보이면 안 된다. "데이터 방향성", "소스 간 일치도", "근거", "추가 확인 필요" 중심으로 표현한다. 금지어: 매수/매도/보유/추천/목표주가/수익률 보장.

---

## 2. 공통 규약

### 2.1 응답 엔벨로프

- 단일 리소스: 객체 직접 반환.
- 컬렉션: `{ "items": [...] }`. 페이지네이션 시 `{ "total", "page", "size", "items" }`.
- 사용자-facing 리포트/대시보드/구독/저널 응답에는 **`notice` 필수**:
  ```json
  { "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다." }
  ```

### 2.2 공통 enum (표준)

| 필드 | 허용값 | 비고 |
|---|---|---|
| `direction` | `positive` `negative` `neutral` `mixed` `unknown` | **소문자 단일 표준**. 기존 목록 API 의 대문자는 정합화 대상(§14) |
| `data_status` | `ok` `partial` `missing` `failed` | |
| `source_agreement` | `HIGH` `MEDIUM` `LOW` | |
| `warning_level` | `NORMAL` `CAUTION` `WARNING` | |
| `user_view` | `watch` `research_more` `not_relevant` | buy/sell/hold 금지 |
| `issued_via` | `free` `subscription` | 리포트 열람 출처 |

- `score`: 0–100(API 원본). 프론트가 /10 등 변환.
- `alignment_rate`: 0–1.

### 2.3 인증 헤더

```http
Authorization: Bearer {access_token}
```

비로그인 허용: `GET /health`, `GET /api/stocks/search`, `GET /api/reports/{stock_code}`(비회원 블라인드), `GET /api/subscriptions/plans`.

### 2.4 에러 응답 (중앙 레지스트리)

```json
{ "detail": { "code": "WATCHLIST_ALREADY_EXISTS", "message": "이미 등록된 관심종목입니다." } }
```

| 코드 | HTTP | 설명 |
|---|---:|---|
| `AUTH_REQUIRED` | 401 | 인증 필요 |
| `TOKEN_EXPIRED` | 401 | access token 만료 |
| `IDENTITY_VERIFICATION_FAILED` | 400 | 포트원 본인인증 검증 실패 |
| `IDENTITY_ALREADY_REGISTERED` | 409 | 이미 가입된 본인인증(핸드폰) |
| `USER_NOT_FOUND` | 404 | 가입되지 않은 사용자(로그인 시) |
| `RISK_AGREEMENT_REQUIRED` | 400 | 위험 고지 동의 누락 |
| `SOCIAL_ALREADY_LINKED` | 409 | 이미 연동된 소셜 계정 |
| `SOCIAL_NOT_LINKED` | 404 | 연동되지 않은 소셜 계정(토큰 로그인 실패) |
| `STOCK_NOT_FOUND` | 404 | 종목 없음 |
| `WATCHLIST_ALREADY_EXISTS` | 409 | 관심종목 중복 |
| `REPORT_NOT_FOUND` | 404 | 발행된 리포트 없음 |
| `REPORT_QUOTA_EXCEEDED` | 402 | 무료 열람 3회 소진(구독 유도) |
| `MEMBERSHIP_REQUIRED` | 401 | 비회원이 잠긴 소스 접근 |
| `JOURNAL_NOT_FOUND` | 404 | 저널 없음 |
| `PLAN_NOT_FOUND` | 404 | 구독 상품 없음 |
| `PAYMENT_VERIFICATION_FAILED` | 400 | 포트원 결제 검증 실패(금액/상태 불일치) |
| `ALREADY_SUBSCRIBED` | 409 | 이미 활성 구독 존재 |
| `ADMIN_AUTH_REQUIRED` | 401 | 관리자 인증 필요 |

---

## 3. 인증·세션 정책 (포트원 본인인증 단일)

회원 아이디/비밀번호는 **DB에 두지 않는다**. 모든 회원 로그인/가입은 포트원 KG이니시스 통합 본인인증으로만 수행한다. 회원가입과 로그인은 **버튼·엔드포인트가 분리**된다.

### 3.1 식별 키

- `users.phone`: 본인인증으로 확보한 핸드폰. 활성 사용자 유니크.
- `users.member_code`: **영문 대문자 4 + 숫자 4 = 8자**(예 `ABCD1234`). 핸드폰 의존성 분산용 내부 유니크 식별자. 가입 시 무작위 생성·충돌 재시도(혼동 문자 0/O/1/I 제외 권장).
- 내부 FK 는 항상 `users.id`.

### 3.2 토큰

| 토큰 | 용도 | 저장 | 만료 |
|---|---|---|---|
| access (JWT HS256) | API 인증 | 클라이언트 | 기본 30분 |
| refresh (랜덤) | access 재발급 | `user_sessions`(해시) + 클라이언트 | 기본 14일 |

401 → refresh 1회 시도 → 실패 시 로그아웃.

### 3.3 회원가입 `POST /api/auth/signup`

포트원 본인인증 완료 후 받은 `imp_uid` 로 서버가 포트원에 검증 호출 → 핸드폰/CI 확보 → 신규 회원 생성.

Request:
```json
{
  "imp_uid": "imp_1234567890",
  "nickname": "사용자",
  "agreed_risk": true,
  "agreed_terms": ["service", "privacy"]
}
```

동작: ① `imp_uid` 포트원 검증(`verification_type='identity'`) → `portone_verifications` 기록 ② 동일 핸드폰 활성 사용자 있으면 `409 IDENTITY_ALREADY_REGISTERED` ③ `member_code` 발급, `users`(phone) 생성 ④ `terms_agreements` 기록 ⑤ 토큰 발급.

Response:
```json
{
  "user": { "id": 1, "member_code": "ABCD1234", "nickname": "사용자", "phone_masked": "010-****-1234", "agreed_risk": true },
  "access_token": "...", "refresh_token": "...", "token_type": "bearer",
  "notice": "..."
}
```

### 3.4 로그인 `POST /api/auth/login`

포트원 본인인증으로 기존 회원 식별.

Request: `{ "imp_uid": "imp_..." }`
동작: `imp_uid` 검증 → 핸드폰으로 활성 사용자 조회 → 없으면 `404 USER_NOT_FOUND`(가입 유도) → 토큰 발급(§3.3 응답과 동일 구조).

### 3.5 `POST /api/auth/refresh` / `POST /api/auth/logout`

- refresh: `{ "refresh_token" }` → 새 access/refresh. 기존 refresh 폐기.
- logout: `{ "refresh_token" }` → 세션 `revoked_at`. 소셜 연동 사용자는 §4.4 사별 로그아웃 동반.

---

## 4. 소셜 로그인 연동 API

소셜은 **편의 로그인 수단**이다. 최초 가입은 반드시 본인인증으로 한 뒤, **로그인된 상태에서만** 소셜 계정을 연동한다. 연동 후에는 본인인증 없이 소셜 토큰으로 로그인할 수 있다.

`provider ∈ { naver, google, kakao }`.

### 4.1 연동 시작/콜백 `POST /api/auth/social/link/{provider}` (인증 필요)

로그인 상태에서 소셜 OAuth 완료 후 콜백.
Request: `{ "code": "<oauth_code>", "redirect_uri": "..." }`
동작: provider 토큰 교환 → `provider_user_id` 획득 → 이미 다른 회원에 연동돼 있으면 `409 SOCIAL_ALREADY_LINKED` → `social_accounts` upsert(access/refresh 토큰 저장).
Response: `{ "provider": "naver", "linked": true, "linked_at": "..." }`

### 4.2 소셜 토큰 로그인 `POST /api/auth/social/login/{provider}` (비인증)

외부에서 소셜 로그인 진입. provider 토큰 교환 → `provider_user_id` 로 `social_accounts` 조회. **연동된 회원이 없으면** `404 SOCIAL_NOT_LINKED`(본인인증 가입 유도). 있으면 해당 회원으로 토큰 발급.

### 4.3 연동 해제 `DELETE /api/auth/social/{provider}` (인증 필요)

`social_accounts` 의 토큰 삭제·행 제거 → 이후 해당 provider 토큰 로그인 차단.
Response: `{ "provider": "kakao", "linked": false }`

### 4.4 로그아웃 사별 처리

서비스 로그아웃 시 연동된 provider 의 정책에 맞춰 처리한다(네이버/구글/카카오 요구 조건 상이). 최소 우리 세션 폐기 + provider 토큰 만료/철회 호출. 상세 분기는 구현 노트로 관리한다.

### 4.5 연동 목록 `GET /api/auth/social` (인증 필요)

`{ "items": [ { "provider": "naver", "linked": true, "linked_at": "..." }, { "provider": "google", "linked": false }, { "provider": "kakao", "linked": false } ] }`

---

## 5. 회원(Users) API

### `GET /api/users/me`
```json
{ "id": 1, "member_code": "ABCD1234", "nickname": "사용자", "phone_masked": "010-****-1234", "agreed_risk": true, "subscription_active": false }
```

### `PATCH /api/users/me`
수정 가능: `nickname`. (핸드폰/본인인증 정보는 재인증 절차로만 변경 — 후속.)
Request: `{ "nickname": "새닉네임" }`

### `DELETE /api/users/me` (회원탈퇴)
soft delete(`users.deleted_at`). 세션 전체 폐기, 소셜 토큰 삭제. 동일 핸드폰 재가입은 partial unique 로 허용.
Response: `{ "status": "deleted" }`

---

## 6. Stock API

### `GET /api/stocks/search?query={query}&limit=20`
종목명/코드 검색(메인 검색 → 종목 매칭). 비로그인 허용.
```json
{ "items": [ { "id": 1, "stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "sector": "반도체" } ] }
```

### `GET /api/stocks?limit=100`
검색 자동완성/시드용 목록.

---

## 7. Watchlist API

관심종목은 **회원/유료 무관 무제한**(기존 10개 한도 폐기). `(user_id, stock_id)` 중복 불가.

### `GET /api/watchlists` (인증)
```json
{ "count": 2, "items": [ { "stock": { "id": 1, "stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "sector": "반도체" }, "created_at": "2026-06-24T10:00:00+09:00" } ] }
```
> `limit` 필드는 더 이상 반환하지 않는다(무제한).

### `POST /api/watchlists` (인증)
Request: `{ "stock_code": "005930" }` → 중복 시 `409 WATCHLIST_ALREADY_EXISTS`.

### `DELETE /api/watchlists/{stock_code}` (인증)
`{ "status": "deleted" }`

---

## 8. 리포트(Report) API — 핵심

리포트는 종목당 **현재 버전**(`final_signals.is_current`)을 백엔드가 사전 생성·저장한 것을 제공한다. 5개 연결점(주식정보/DART/채용공고/네이버키워드/증권사리포트) 각각의 LLM 요약 + 종합점수로 구성되며, 각 연결점은 소스 상세 페이지로 연결된다.

### 8.1 리포트 조회 `GET /api/reports/{stock_code}`

비로그인 허용(비회원=블라인드). 로그인 시 잠금 해제 상태에 따라 전체/요약을 반환.

회원·언락(또는 구독) 응답:
```json
{
  "stock": { "id": 10, "stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI", "sector": "반도체" },
  "report_version": { "final_signal_id": 100, "run_key": "AGGREGATED", "signal_date": "2026-06-24", "updated_at": "2026-06-24T08:00:00+09:00" },
  "direction": "positive",
  "score": 72,
  "alignment_rate": 0.8,
  "source_agreement": "MEDIUM",
  "warning_level": "NORMAL",
  "data_status": "ok",
  "summary": "여러 데이터 소스에서 유사한 방향성이 관찰됩니다.",
  "sources": [
    { "source": "price",   "direction": "positive", "score": 70, "data_status": "ok",      "summary": "...", "locked": false },
    { "source": "dart",    "direction": "neutral",  "score": 50, "data_status": "ok",      "summary": "...", "locked": false },
    { "source": "hiring",  "direction": "positive", "score": 80, "data_status": "ok",      "summary": "...", "locked": false },
    { "source": "datalab", "direction": "positive", "score": 65, "data_status": "ok",      "summary": "...", "locked": false },
    { "source": "report",  "direction": "neutral",  "score": null, "data_status": "missing","summary": null,  "locked": false }
  ],
  "access": { "unlocked": true, "issued_via": "free", "is_member": true },
  "notice": "..."
}
```

#### 비회원 블라인드 규칙
비로그인 호출 시: `dart`·`datalab` 소스만 전체 공개. 그 외(`price`/`hiring`/`report`)와 **종합 `direction`/`score`/`alignment_rate`/`summary`** 는 마스킹.
```json
{
  "stock": { "...": "..." },
  "direction": null, "score": null, "alignment_rate": null, "summary": null,
  "sources": [
    { "source": "dart",    "direction": "neutral",  "score": 50, "summary": "...", "locked": false },
    { "source": "datalab", "direction": "positive", "score": 65, "summary": "...", "locked": false },
    { "source": "price",   "locked": true },
    { "source": "hiring",  "locked": true },
    { "source": "report",  "locked": true }
  ],
  "access": { "unlocked": false, "is_member": false },
  "notice": "전체 리포트는 로그인 후 무료 3회까지 열람할 수 있습니다."
}
```

회원이지만 **현재 버전 미언락**일 때도 같은 블라인드 형태(단, `dart`/`datalab` + 안내) + `access.is_member=true` 로 반환하고, 프론트가 "발행(열람)" 버튼을 노출한다.

### 8.2 열람(언락) `POST /api/reports/{stock_code}/issue` (인증)

현재 버전 리포트를 잠금 해제한다. **즉시 응답**(비동기 job 아님).

동작:
1. 종목의 현재 버전 `final_signal_id` 조회(없으면 `404 REPORT_NOT_FOUND`).
2. `report_issuances` 에 `(user_id, final_signal_id)` 존재 → 이미 언락(무차감) → 200.
3. 미존재 시: 구독 active 면 `issued_via='subscription'` 으로 기록(무료 불변). 비구독이면 무료 잔여 확인 → 0 이면 `402 REPORT_QUOTA_EXCEEDED`(구독 유도), >0 이면 `issued_via='free'` 기록.
4. 전체 리포트(§8.1 언락 형태) 반환.

Response: §8.1 회원 언락 응답 + `"access": { "unlocked": true, "issued_via": "free", "free_remaining": 2 }`.

> 동일 버전 재열람은 무차감. 실시간 변동으로 **새 버전**이 생기면 새 `final_signal_id` 이므로 다시 1회 차감.

### 8.3 쿼터 조회 `GET /api/reports/quota` (인증)
```json
{ "free_quota": 3, "free_used": 1, "free_remaining": 2, "subscription_active": false, "notice": "..." }
```

### 8.4 소스 상세 `GET /api/reports/{stock_code}/sources/{source}`

`source ∈ { price, dart, hiring, datalab, report }`. 해당 원천 데이터 상세 + LLM 상세 요약. 클릭 상세 페이지용.

접근 규칙: 비회원은 `dart`·`datalab` 만 200, 나머지는 `401 MEMBERSHIP_REQUIRED`. 회원은 현재 버전 언락(또는 구독) 시 전체, 미언락이면 `dart`/`datalab` 외 `402/blinded`.

예(`dart`):
```json
{
  "stock": { "stock_code": "005930", "stock_name": "삼성전자" },
  "source": "dart",
  "direction": "neutral", "score": 50, "data_status": "ok",
  "summary": "최근 공시에서 중립적 신호가 확인됩니다.",
  "items": [
    { "title": "주요사항보고서", "event_date": "2026-06-20", "disclosure_type": "정정공시", "evidence_url": "https://dart.fss.or.kr/...", "is_official": true }
  ],
  "notice": "..."
}
```
소스별 `items` 스키마는 원천 테이블을 따른다(price=시세/재무 지표, hiring=공고수/증감, datalab=검색지수/급등, report=증권사/목표가/의견). 매핑은 [db-schema-spec.md](./db-schema-spec.md) §6.

### 8.5 레거시 호환

`GET /signals/{ticker}` 는 호환 라우트로 유지(원형 `final_signals` 행). 신규 화면은 `GET /api/reports/...` 를 사용한다.

---

## 9. 저널(Journal) API

발행(열람)한 리포트를 저장해 투자 추이를 기록한다. 발행 시점 스냅샷(`final_signal_id`, score/방향/시점)을 함께 저장.

### `GET /api/journals?stock_code=&limit=20` (인증)
### `POST /api/journals` (인증)
```json
{ "stock_code": "005930", "final_signal_id": 100, "user_view": "research_more", "memo": "Report 데이터 없어 추가 확인", "tags": ["DART"] }
```
- `user_view` 화이트리스트(watch/research_more/not_relevant). buy/sell/hold → `400 INVALID_USER_VIEW`.
### `GET|PATCH|DELETE /api/journals/{journal_id}` (인증, 본인만)
PATCH 수정: `user_view`, `memo`, `tags`. DELETE: hard delete(MVP).

---

## 10. 구독·결제(Subscription/Payment) API

단일 상품 `monthly_9900`(월 9,900원, 무제한 열람). 포트원 KG이니시스 일반결제(결제창 + API).

### `GET /api/subscriptions/plans` (비인증)
활성 플랜만. `{ "plans": [ { "plan_type": "monthly_9900", "plan_display_name": "월 구독", "price_monthly": 9900, "has_alt_data": true, "has_detail_report": true } ] }`

### `GET /api/subscriptions/me` (인증)
`{ "subscription": { "plan_type": "monthly_9900", "status": "active", "started_at": "...", "expires_at": "..." } | null, "notice": "..." }`

### `POST /api/payments/checkout` (인증)
결제 시작. `merchant_uid` 생성·반환(포트원 결제창 파라미터).
Response: `{ "merchant_uid": "sa_pay_20260624_...", "amount": 9900, "name": "Signal Alpha 월 구독", "pg": "html5_inicis" }`

### `POST /api/payments/confirm` (인증)
결제창 성공 후 `imp_uid` 서버 검증.
Request: `{ "imp_uid": "imp_...", "merchant_uid": "sa_pay_..." }`
동작: 포트원 결제건 조회 → **금액(9900)·상태(paid) 검증** 실패 시 `400 PAYMENT_VERIFICATION_FAILED` → `portone_verifications(verification_type='payment')` 기록 → 활성 구독 있으면 `409 ALREADY_SUBSCRIBED` → `signal_subscriptions` active·`expires_at = now()+30d` 생성.
Response: `{ "subscription": { "plan_type": "monthly_9900", "status": "active", "expires_at": "..." }, "notice": "..." }`

### `POST /api/payments/cancel` (인증)
포트원 결제 취소 API 호출 + 구독 `status='cancelled'`, `cancelled_at`.
> 구독 종료/취소 후에도 **무료 잔여분(3회 중 미사용분)은 그대로 사용 가능**(`report_issuances.issued_via='free'` 카운트만으로 잔여 도출).

---

## 11. 관리자(Admin) API

관리자는 하드코딩 계정(`admin_accounts`)으로만 로그인한다. 회원가입 없음. 별도 세션 토큰(`admin_sessions`).

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/admin/login` | `{email,password}` → `{session_token, expires_at, admin}` |
| POST | `/api/admin/logout` | 세션 폐기 |
| GET | `/api/admin/users?page=1&size=20&q=` | 회원 목록(페이지네이션·검색) |
| GET | `/api/admin/users/{user_id}` | 회원 상세(구독 포함) |
| POST | `/api/admin/users/{user_id}/subscription` | 구독 등록 |
| PUT | `/api/admin/users/{user_id}/subscription` | 구독 수정 |
| DELETE | `/api/admin/users/{user_id}/subscription` | 구독 취소 |
| GET | `/api/admin/stats` | 총매출/MRR/구독자수 |

관리자 API 는 `Authorization: Bearer {session_token}` 사용, 일반 사용자 JWT 와 구분.

---

## 12. 분석 상태(Analytics) API

`GET /api/analytics/{ticker}/status` — 배치 파이프라인 신선도 표시용(선택). 리포트 열람 모델은 비동기 job 이 아니므로 폴링은 선택적이다.
```json
{ "ticker": "005930", "overall": "success", "stages": [ { "task_type": "AGGREGATE", "status": "success", "updated_at": "..." } ], "notice": "..." }
```

---

## 13. DB 변경 요약

상세는 [db-schema-spec.md](./db-schema-spec.md). 신규 마이그레이션:

| 파일 | 변경 |
|---|---|
| `025_users_phone.sql` | `users.phone` + 활성 사용자 partial unique |
| `026_report_issuances.sql` | 리포트 열람 쿼터 테이블(`(user_id, final_signal_id)` 멱등, `issued_via`) |
| `027_subscription_single_product.sql` | 단일 상품 `monthly_9900`, `free` 무제한, `pro`/`premium` 비활성 |

재사용(변경 없음): `portone_verifications`, `social_accounts`, `terms_agreements`, `signal_subscriptions`, `subscription_plans`, `admin_*`, `user_sessions`, `watchlists`, `signal_journals`, `final_signals`, 원천 raw 테이블.

---

## 14. 구현 정합화 과제 (현 코드 → 목표)

- **교체**
  - `auth.py`: 이메일/비밀번호 가입·로그인 제거 → 포트원 본인인증 가입/로그인(`imp_uid` 검증). 회원가입/로그인 엔드포인트 분리.
  - `watchlists.py`: `WATCHLIST_LIMIT=10` 및 한도 검사 제거(무제한). 응답에서 `limit` 제거.
- **신규**
  - 소셜 연동/토큰 로그인/해제(`/api/auth/social/*`).
  - 리포트 도메인(`/api/reports/*`): 조회·열람(쿼터)·소스 상세·비회원 블라인드 + `report_issuances` 리포지토리.
  - 결제(`/api/payments/*`): 포트원 결제 검증/취소.
  - `PATCH /api/users/me`, `DELETE /api/users/me`(탈퇴), `member_code`(영문4+숫자4) 생성기, `phone` 저장.
  - 관리자 구독 등록/수정/취소, `POST /api/admin/logout`.
- **표준화**
  - `direction` 소문자 단일화(기존 `GET /api/signals` 목록 대문자 정리).
  - 사용자-facing 응답 `notice` 필수.

---

## 15. 구현 우선순위 / 테스트 기준

**우선순위**: ① 본인인증 가입/로그인 + 토큰 → ② 관심종목(무제한) → ③ 리포트 조회/열람/쿼터 + 비회원 블라인드 → ④ 소스 상세 5종 → ⑤ 결제 검증/취소 → ⑥ 소셜 연동/해제 → ⑦ 저널 → ⑧ 관리자.

**테스트 기준**
- 인증: `imp_uid` 검증 성공 가입, 동일 핸드폰 재가입 차단(활성), 탈퇴 후 재가입 허용, 미가입 로그인 `404`.
- 관심종목: 한도 없음(11개 이상 추가 가능), 중복 차단.
- 리포트: 비회원 블라인드(dart/datalab만), 회원 무료 3회 차감, 동일 버전 재열람 무차감, 새 버전 재차감, 소진 시 `402`, 구독자 무제한+무료 불변.
- 결제: 금액/상태 위변조 거부, 활성 구독 중복 차단, 취소 후 무료 잔여 유지.
- 소셜: 로그인 상태에서만 연동, 미연동 토큰 로그인 `404`, 해제 후 차단.
- 관리자: 하드코딩 로그인, 구독 CRUD, 매출 집계.
