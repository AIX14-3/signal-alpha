# 외부 연동 설정 가이드 (포트원 본인인증·결제 / 소셜 OAuth)

> 기준일: 2026-06-24
> 대상: `services/main-server`(백엔드), `web`(프론트엔드), 각 provider 개발자 콘솔
> 목적: 포트원(본인인증·결제)과 소셜 로그인(네이버/구글/카카오) 실연동에 필요한 콘솔 등록값·콜백 URL·환경변수·동작 모드를 한 곳에 정리한다.
> 연관 문서: [main-server-api-spec.md](./main-server-api-spec.md), [web-frontend-spec.md](./web-frontend-spec.md)

---

## 0. 공통 원칙

- **로컬 개발 주소**: 프론트 `http://localhost:3000`, 백엔드 `http://localhost:8000`.
  - 브라우저는 반드시 `localhost`로 접속(`127.0.0.1` 사용 시 CORS·redirect_uri 불일치).
- **dev 모드**: 관련 키를 설정하지 않으면 외부 호출 없이 **결정적 모의값**으로 동작한다. 실 키를 넣으면 자동으로 실 연동 경로로 전환된다(`config.py`/프론트 `portone.ts`·`social.ts` 분기). → 키 없이도 로컬 전 기능 시연 가능.
- **개인정보 최소 수집**: 소셜은 OAuth 토큰 + 계정 고유 `id`(식별자)만 사용한다. 이메일·전화·프로필 등은 동의/수집하지 않는다. phone(연락처)의 정본은 **포트원 본인인증**이다.
- redirect_uri / 콜백 URL은 **콘솔·인증요청·토큰교환 3곳이 글자 그대로 동일**해야 한다.

---

## 1. 포트원 V2 — 본인인증 + 결제

> **V2 기준.** 베이스 `https://api.portone.io`, 서버 인증헤더 `Authorization: PortOne {API_SECRET}`.
> 프론트는 `@portone/browser-sdk` v2(`requestIdentityVerification`/`requestPayment`)를 사용하고
> **storeId + channelKey**로 식별한다.

### 1.1 환경변수

| 위치 | 변수 | 값/설명 |
|---|---|---|
| 프론트 | `NEXT_PUBLIC_PORTONE_STORE_ID` | V2 **Store ID**(`store-...`). 미설정 시 프론트 dev 모드 |
| 프론트 | `NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY` | **KG이니시스 통합인증** 채널키(본인인증) |
| 프론트 | `NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT` | **이니시스 일반결제** 채널키(결제) |
| 백엔드 | `PORTONE_API_BASE` | 기본 `https://api.portone.io` |
| 백엔드 | `PORTONE_API_SECRET` | V2 **API Secret**(서버 검증용) — 미설정 시 백엔드 dev 모드 |
| 백엔드 | `PORTONE_STORE_ID` | (선택) Store ID |
| 백엔드 | `SUBSCRIPTION_PRICE_KRW` | 구독가(기본 `9900`) — 결제 검증 금액 |
| 백엔드 | `FREE_REPORT_QUOTA` | 무료 열람 횟수(기본 `3`) |

> 백엔드 `PORTONE_API_SECRET` 가 있으면 실 모드. 없으면 dev 모드(`config.py: portone_dev_mode`).
> 프론트는 `NEXT_PUBLIC_PORTONE_STORE_ID` 가 있으면 실 SDK, 없으면 dev 모드.

### 1.2 포트원 콘솔 설정 (V2)

1. 관리자 콘솔 → 연동 정보 → **Store ID** 확인, **API Secret** 발급(V2).
2. **본인인증 채널**: 채널 추가 → **KG이니시스 통합인증** → 채널키 확인 → `NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY`.
3. **결제 채널**: 채널 추가 → **이니시스 일반결제(결제창/API 일반결제)** → 채널키 확인 → `NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT`.

### 1.3 본인인증 흐름 (회원가입/로그인)

```
프론트: identityVerificationId 생성 → PortOne.requestIdentityVerification({ storeId, channelKey, identityVerificationId })
  → 백엔드 POST /api/auth/signup | /api/auth/login  { identity_verification_id, ... }
  → 백엔드 verify_identity: GET {API_BASE}/identity-verifications/{id}  (Authorization: PortOne {secret})
  → status=="VERIFIED" → verifiedCustomer.phoneNumber(숫자만 정규화) + ci → users 생성/조회
```
- dev 모드: `identity_verification_id`는 기기 고정 가상값(localStorage `sa_dev_identity_id`), phone은 id 해시로 결정적 도출.

### 1.4 결제 흐름 (구독)

```
프론트 POST /api/payments/checkout → { payment_id, amount, order_name, currency:"CURRENCY_KRW" }
  → PortOne.requestPayment({ storeId, channelKey, paymentId, orderName, totalAmount, currency:"KRW", payMethod:"CARD" })
  → 백엔드 POST /api/payments/confirm { payment_id }
  → verify_payment: GET {API_BASE}/payments/{payment_id}
  → status=="PAID" & amount.total==SUBSCRIPTION_PRICE_KRW 검증 → 구독 active(30일)
취소: POST /api/payments/cancel → POST {API_BASE}/payments/{payment_id}/cancel { reason }
```
- dev 모드: 실제 결제창 없이 `amount=상품가`, `status="paid"`로 검증 통과.

---

## 2. 소셜 OAuth (네이버 / 구글 / 카카오) — 토큰 + 고유 id만

소셜은 **편의 로그인** 용도다. OAuth 토큰과 provider 계정 **고유 id**(식별자)만 사용하고, 이메일·전화·프로필은 동의·수집하지 않는다. 백엔드 `social.py`는 `provider_user_id`만 추출한다.

### 2.1 공통 값

| 항목 | 값 |
|---|---|
| 서비스/사이트 URL | `http://localhost:3000` |
| Callback(Redirect) URI 패턴 | `http://localhost:3000/auth/callback/{provider}` |
| 백엔드 env | `{PROVIDER}_CLIENT_ID`, `{PROVIDER}_CLIENT_SECRET` (provider = `NAVER`/`GOOGLE`/`KAKAO`) |

> provider별 키를 설정하면 그 provider만 dev 모드 해제 → 실 OAuth(`social.py: is_dev_mode`).

### 2.2 네이버 (developers.naver.com → 애플리케이션 등록)

| 항목 | 값 |
|---|---|
| 사용 API | 네이버 로그인 |
| 서비스 URL | `http://localhost:3000` |
| **Callback URL** | `http://localhost:3000/auth/callback/naver` |
| 제공 정보(동의항목) | 추가 안 함 — `id`만 사용(항상 반환) |
| 인증요청 | `https://nid.naver.com/oauth2.0/authorize?response_type=code&client_id={ID}&redirect_uri=http://localhost:3000/auth/callback/naver&state={RANDOM}` |
| 토큰 | `https://nid.naver.com/oauth2.0/token` |
| 프로필 | `https://openapi.naver.com/v1/nid/me` (→ `response.id`) |
| env | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` |

### 2.3 구글 (console.cloud.google.com → OAuth 클라이언트 ID·웹 애플리케이션)

| 항목 | 값 |
|---|---|
| 승인된 JavaScript 원본 | `http://localhost:3000` |
| **승인된 리디렉션 URI** | `http://localhost:3000/auth/callback/google` |
| scope | `openid` (이메일·프로필 미요청 → 고유 `sub`만) |
| OAuth 동의화면 | 외부 앱이면 테스트 사용자에 본인 계정 등록 |
| 인증요청 | `https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id={ID}&redirect_uri=http://localhost:3000/auth/callback/google&scope=openid&state={RANDOM}` |
| 토큰 | `https://oauth2.googleapis.com/token` |
| 식별자 | `id_token`의 `sub` (또는 `https://www.googleapis.com/oauth2/v2/userinfo` 의 `id`) |
| env | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` |

> 구글은 `openid`만일 때 userinfo가 비어 있을 수 있으므로 식별자는 `id_token`의 `sub`로 처리하는 보완이 필요(실 OAuth 구현 시 반영).

### 2.4 카카오 (developers.kakao.com → 내 애플리케이션)

| 항목 | 값 |
|---|---|
| 카카오 로그인 | 활성화 ON |
| 플랫폼 → Web 사이트 도메인 | `http://localhost:3000` |
| **Redirect URI** | `http://localhost:3000/auth/callback/kakao` |
| 동의항목 | 없음 — `id`만 사용 |
| 인증요청 | `https://kauth.kakao.com/oauth/authorize?response_type=code&client_id={REST_API_KEY}&redirect_uri=http://localhost:3000/auth/callback/kakao&state={RANDOM}` |
| 토큰 | `https://kauth.kakao.com/oauth/token` |
| 프로필 | `https://kapi.kakao.com/v2/user/me` (→ `id`) |
| env | `KAKAO_CLIENT_ID`(= REST API 키), `KAKAO_CLIENT_SECRET`(보안→Client Secret 사용 시) |

### 2.5 소셜 연동/로그인 흐름

```
[연동] 로그인 상태 → provider 인증요청 리다이렉트 → /auth/callback/{provider}?code=…
  → 프론트가 code 를 백엔드 POST /api/auth/social/link/{provider} { code, redirect_uri } 로 전달
  → social.py 토큰교환 → 고유 id → social_accounts upsert
[로그인] /auth/callback/{provider}?code=… → POST /api/auth/social/login/{provider}
  → 고유 id 로 연동 계정 조회 → 우리 토큰 발급 (미연동이면 404 SOCIAL_NOT_LINKED)
[해제] DELETE /api/auth/social/{provider} → 토큰/행 삭제
```
- dev 모드: provider별 기기 고정 가상 code(localStorage `sa_social_{provider}`)로 연동↔로그인 매칭.

---

## 3. 환경변수 한눈에 (.env 예시)

```dotenv
# --- 백엔드(services/main-server) ---
DATABASE_URL=postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha
CORS_ALLOW_ORIGINS=http://localhost:3000
SUBSCRIPTION_PRICE_KRW=9900
FREE_REPORT_QUOTA=3
# 포트원
PORTONE_API_BASE=https://api.iamport.kr
PORTONE_API_KEY=
PORTONE_API_SECRET=
# 소셜(설정한 provider만 실 OAuth)
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
KAKAO_CLIENT_ID=
KAKAO_CLIENT_SECRET=

# --- 프론트(web) ---
NEXT_PUBLIC_MAIN_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_PORTONE_IMP_CODE=
```

---

## 4. 남은 구현 (실 연동 활성화에 필요)

현재 코드는 dev 모드 경로가 완비돼 있고, 실 OAuth/모바일 결제 리다이렉트는 아래가 추가로 필요하다.

1. **프론트 OAuth 콜백 라우트** `web/src/app/auth/callback/[provider]/page.tsx` — provider 리다이렉트(`?code=&state=`)에서 `code` 추출 → 백엔드 `social/link|login` 호출.
2. **소셜 인증요청 리다이렉트** — 로그인/마이페이지 소셜 버튼이 §2.2~2.4 인증요청 URL로 이동(`state` CSRF 포함).
3. **백엔드 식별자 보완** — 구글 `openid` 단독 시 `id_token.sub` 파싱(`social.py`).
4. **(선택) 모바일 결제 리다이렉트** — `m_redirect_url` 처리 페이지.

> 위 1~3을 구현하면 §2 콘솔 값으로 네이버/구글/카카오 실 로그인이 동작한다. 포트원은 `NEXT_PUBLIC_PORTONE_IMP_CODE` + REST key/secret + KG이니시스 채널 설정만으로 본인인증/결제가 실 모드로 전환된다.
