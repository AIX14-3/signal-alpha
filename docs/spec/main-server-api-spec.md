# Signal Alpha Main Server API 스펙

> 기준일: 2026-06-17 (개정: 2026-06-22 — `GET /api/signals` 목록 엔드포인트 구현 반영)
> 대상: `services/main-server`
> 목적: Web 화면 디자인 확정 전, 사용자-facing API와 인증/세션/관심종목/시그널/저널 계약을 먼저 고정한다.

---

## 1. 범위와 원칙

Main Server는 Web과 외부 클라이언트가 호출하는 사용자-facing API 경계다.

Main Server가 담당한다.

- 회원가입, 로그인, 로그아웃, 현재 사용자 조회
- 관심종목 등록, 조회, 삭제
- 관심종목 기반 대시보드 조회
- 시그널 실행 요청과 작업 상태 조회
- 최신 시그널, 종목별 시그널, 시그널 상세 조회
- Signal Journal 작성, 조회, 수정, 삭제
- 읽음 상태, 사용자별 데이터 접근 제어

Main Server가 하지 않는다.

- 외부 데이터 수집
- 공시/리포트/가격/대체데이터 분석
- LLM/RAG 호출
- agent-worker 내부 큐 처리
- 최종 시그널 생성 로직 보유

수집과 분석은 `agent-worker`가 담당한다. Main Server는 저장된 DB 결과를 읽거나, 내부 worker API에 분석 작업을 요청한다.

모든 사용자-facing 문구와 API 필드는 투자 추천처럼 보이면 안 된다. API 응답은 "데이터 방향성", "소스 간 일치도", "근거", "추가 확인 필요"를 중심으로 구성한다.

---

## 2. 확정된 MVP 결정

| 항목 | 결정 |
|---|---|
| 로그인 방식 | 이메일/비밀번호 |
| 소셜 로그인 | 후속 확장. 내부 식별자는 항상 `users.id` |
| 토큰 방식 | access token + refresh token |
| 세션 저장 | refresh token은 `user_sessions`에 저장 |
| 로그아웃 | 서버 세션 폐기 |
| 회원가입 필수값 | `email`, `password`, `agreed_risk=true` |
| 회원가입 선택값 | `nickname` |
| 관심종목 제한 | 사용자당 최대 10개 |
| 관심종목 중복 | `(user_id, stock_id)` 중복 불가 |
| 대시보드 기준 | 내 관심종목 + 각 종목 최신 시그널 |
| Source 요약 | `DART`, `PRICE`, `REPORT`, `ALTERNATIVE` 모두 반환 |
| Source 미수집 상태 | `data_status="missing"` |
| 시그널 실행 | 비동기 job 방식 |
| 저널 `user_view` | `watch`, `research_more`, `not_relevant` |

---

## 3. 인증/세션 정책

### 3.1 토큰

| 토큰 | 용도 | 저장 위치 | 권장 만료 |
|---|---|---|---|
| access token | API 인증 | 클라이언트 메모리 또는 보안 저장소 | 15~30분 |
| refresh token | access token 재발급 | 서버 `user_sessions` + 클라이언트 | 7~30일 |

모든 사용자 API는 아래 헤더를 요구한다.

```http
Authorization: Bearer {access_token}
```

비로그인 허용 후보:

- `GET /api/stocks/search`
- `GET /health`

### 3.2 사용자 세션 테이블

현재 DB에는 일반 사용자용 세션 테이블이 없다. 다음 migration이 필요하다.

```sql
CREATE TABLE user_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    user_agent TEXT,
    ip_address INET,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_user_sessions_user
    ON user_sessions (user_id, created_at DESC);

CREATE INDEX idx_user_sessions_expires_at
    ON user_sessions (expires_at);
```

refresh token 원문은 저장하지 않고 hash만 저장한다.

### 3.3 비밀번호

- DB에는 `password_hash`만 저장한다.
- 비밀번호 최소 길이는 8자다.
- 이메일은 소문자로 normalize한다.
- `agreed_risk=false`인 회원가입은 거부한다.

---

## 4. 공통 응답 규칙

### 4.1 공통 notice

사용자-facing 주요 응답에는 다음 성격의 고지를 포함한다.

```json
{
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

### 4.2 데이터 상태

```text
ok
partial
missing
failed
```

| 값 | 의미 |
|---|---|
| `ok` | 표시 가능한 데이터가 정상적으로 존재 |
| `partial` | 일부 source나 근거가 부족하지만 요약 가능 |
| `missing` | 해당 source 데이터가 아직 없음 |
| `failed` | 수집/분석 또는 조회 실패 |

### 4.3 에러 응답

```json
{
  "error": {
    "code": "WATCHLIST_LIMIT_EXCEEDED",
    "message": "관심종목은 최대 10개까지 등록할 수 있습니다.",
    "details": {}
  },
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

대표 에러 코드:

| 코드 | HTTP | 설명 |
|---|---:|---|
| `AUTH_REQUIRED` | 401 | 인증 필요 |
| `INVALID_CREDENTIALS` | 401 | 이메일 또는 비밀번호 불일치 |
| `TOKEN_EXPIRED` | 401 | access token 만료 |
| `FORBIDDEN` | 403 | 다른 사용자의 리소스 접근 |
| `RISK_AGREEMENT_REQUIRED` | 400 | 고지 동의 누락 |
| `STOCK_NOT_FOUND` | 404 | 종목 없음 |
| `WATCHLIST_LIMIT_EXCEEDED` | 400 | 관심종목 10개 초과 |
| `WATCHLIST_ALREADY_EXISTS` | 409 | 관심종목 중복 |
| `SIGNAL_NOT_FOUND` | 404 | 시그널 없음 |
| `JOB_NOT_FOUND` | 404 | 작업 없음 |
| `WORKER_UNAVAILABLE` | 503 | worker 호출 실패 |

---

## 5. Auth API

### `POST /api/auth/signup`

회원가입과 동시에 로그인 토큰을 발급한다.

Request:

```json
{
  "email": "user@example.com",
  "password": "password123",
  "nickname": "사용자",
  "agreed_risk": true
}
```

Response:

```json
{
  "user": {
    "id": 1,
    "email": "user@example.com",
    "nickname": "사용자",
    "agreed_risk": true
  },
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

### `POST /api/auth/login`

Request:

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response는 회원가입과 동일한 토큰 응답 구조를 사용한다.

### `POST /api/auth/refresh`

Request:

```json
{
  "refresh_token": "..."
}
```

Response:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer"
}
```

refresh 시 기존 refresh token은 폐기하고 새 세션 또는 새 refresh token hash로 교체한다.

### `POST /api/auth/logout`

현재 refresh token 또는 현재 세션을 폐기한다.

Request:

```json
{
  "refresh_token": "..."
}
```

Response:

```json
{
  "status": "ok"
}
```

### `GET /api/users/me`

Response:

```json
{
  "id": 1,
  "email": "user@example.com",
  "nickname": "사용자",
  "agreed_risk": true,
  "is_verified": false
}
```

---

## 6. Stock API

### `GET /api/stocks/search?query={query}`

종목명 또는 종목코드로 검색한다. MVP에서는 `stocks` 테이블에 존재하는 종목만 반환한다.

Response:

```json
{
  "items": [
    {
      "id": 1,
      "stock_code": "005930",
      "stock_name": "삼성전자",
      "market": "KOSPI",
      "sector": "반도체"
    }
  ]
}
```

---

## 7. Watchlist API

### 정책

- 로그인 사용자만 사용 가능하다.
- 최대 10개까지 등록할 수 있다.
- 같은 사용자의 같은 종목은 중복 등록할 수 없다.
- `stocks`에 존재하는 종목만 등록할 수 있다.
- 기본 정렬은 최근 추가순이다.
- 알림 설정은 MVP에서 기본 `false`다.

현재 DB/Repository 차이:

- `watchlists.notification_enabled` DB 기본값은 현재 `TRUE`다.
- `UserSignalRepository.add_watchlist()` 기본값도 현재 `True`다.
- MVP 정책을 맞추려면 migration 또는 repository 기본값 조정이 필요하다.
- `subscription_plans.max_watchlist` 기본값은 현재 3이다. MVP 무료 플랜 기준 10으로 seed 또는 정책 조정이 필요하다.

### `GET /api/watchlists`

Response:

```json
{
  "limit": 10,
  "count": 2,
  "items": [
    {
      "stock": {
        "id": 1,
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "market": "KOSPI",
        "sector": "반도체"
      },
      "notification_enabled": false,
      "created_at": "2026-06-17T10:00:00+09:00"
    }
  ]
}
```

### `POST /api/watchlists`

Request:

```json
{
  "stock_code": "005930"
}
```

Response:

```json
{
  "stock": {
    "id": 1,
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "market": "KOSPI"
  },
  "notification_enabled": false,
  "created_at": "2026-06-17T10:00:00+09:00"
}
```

### `DELETE /api/watchlists/{stock_code}`

Response:

```json
{
  "status": "deleted"
}
```

---

## 8. Dashboard API

### `GET /api/dashboard`

내 관심종목과 각 종목의 최신 시그널 요약을 한 번에 반환한다.

Response:

```json
{
  "user": {
    "id": 1,
    "email": "user@example.com",
    "nickname": "사용자"
  },
  "watchlist_limit": 10,
  "watchlist_count": 3,
  "items": [
    {
      "stock": {
        "id": 1,
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "market": "KOSPI"
      },
      "latest_signal": {
        "signal_id": 100,
        "direction": "positive",
        "score": 72,
        "alignment_rate": 0.8,
        "data_status": "ok",
        "needs_review": false,
        "summary": "공식 공시와 가격 데이터에서 같은 방향성이 확인되었습니다.",
        "source_count": 2,
        "updated_at": "2026-06-17T10:00:00+09:00"
      },
      "source_summary": [
        {
          "source": "DART",
          "direction": "neutral",
          "score": 50,
          "data_status": "ok"
        },
        {
          "source": "PRICE",
          "direction": "positive",
          "score": 72,
          "data_status": "ok"
        },
        {
          "source": "REPORT",
          "direction": "unknown",
          "score": null,
          "data_status": "missing"
        },
        {
          "source": "ALTERNATIVE",
          "direction": "unknown",
          "score": null,
          "data_status": "missing"
        }
      ],
      "journal": {
        "has_journal": true,
        "latest_journal_id": 20
      }
    }
  ],
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

`source_summary`는 항상 `DART`, `PRICE`, `REPORT`, `ALTERNATIVE` 네 항목을 반환한다. 데이터가 없으면 `direction="unknown"`, `score=null`, `data_status="missing"`으로 둔다.

API 응답에는 `confidence`라는 필드를 노출하지 않는다. 내부 DB에 유사 필드가 있어도 사용자-facing 응답에서는 `score`, `alignment_rate`, `data_status`, `needs_review` 중심으로 표현한다.

---

## 9. Signal API

### `POST /api/signals/run/{stock_code}`

비동기 분석 job을 생성한다. 즉시 최종 결과를 반환하지 않는다.

동작:

1. 사용자 인증을 확인한다.
2. `stocks`에서 종목을 찾는다.
3. `analysis_requests`에 job을 생성한다.
4. agent-worker 내부 API에 수집/분석 작업을 요청한다.
5. `job_id`를 반환한다.

Response:

```json
{
  "job_id": 123,
  "stock_code": "005930",
  "status": "queued",
  "message": "분석 요청이 등록되었습니다.",
  "notice": "이 결과는 투자 추천이 아니라 데이터 방향성과 근거 확인을 위한 정보입니다."
}
```

현재 DB/Repository 차이:

- `analysis_requests.status`는 현재 `pending`, `running`, `completed`, `failed`만 허용한다.
- API 확정 상태값은 `queued`, `running`, `completed`, `partial`, `failed`, `cancelled`다.
- 구현 시 DB CHECK 제약을 확장하거나 API에서 `pending -> queued`로 매핑해야 한다.

### `GET /api/jobs/{job_id}`

Response:

```json
{
  "job_id": 123,
  "stock_code": "005930",
  "status": "running",
  "progress": {
    "DART": "completed",
    "PRICE": "running",
    "REPORT": "pending",
    "ALTERNATIVE": "missing"
  },
  "latest_signal_id": null,
  "error_message": null,
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

### `GET /api/signals` (구현됨, 2026-06-22)

현재 발행(`is_published`) 중인 최신 시그널 목록을 **종목당 1개**로 반환한다. 인증 필요.

`final_signals`는 대체데이터 소스별(`run_key` = HIRING/PATENT/DATALAB)로 **종목당 여러 행**(모델 B)이
적재되므로, 이 엔드포인트가 **API 레이어에서 `stock_id` 기준 런타임 그룹핑**해 종목당 1개로 응집한다
(DB 스키마/리네임 변경 없음 — "모델 A shape"으로 노출).

Query:

```text
stock_ids=1,2,3      # 옵션. 콤마 구분 정수(관심종목 필터). 파싱 후 빈 목록이면 [] 반환
```

> **필터 구현 주의:** 스펙 초안의 `watchlist_only=true`(서버가 내 관심종목으로 자동 필터)는 **아직 미구현**이다.
> 현재는 클라이언트(프론트)가 자신의 관심종목 `stock_id` 들을 모아 `stock_ids` 로 **명시 전달**하는 구조다.
> 서버측 watchlist 자동 필터가 필요해지면 별도 추가한다.

집계 규칙(종목 내 소스 행들을 1개로 합성):

| 필드 | 규칙 |
|---|---|
| `direction` | 소스 방향 다수결(동률 → `NEUTRAL`). **대문자 반환** |
| `score` | 가용 소스 `final_score`(0~100) 평균 |
| `alignment_rate` | 소스 `consensus_score`(폴백 `confidence`) 평균 ÷ 100 |
| `source_agreement` | 소스 중 **가장 보수적**(낮은 합의: LOW>MEDIUM>HIGH) |
| `warning_level` / `data_status` | 가장 보수적 채택(WARNING→`failed`, CAUTION/needs_review→`partial`) |
| `summary` | 기준행(최신 published) 요약 |
| `score_breakdown.alternative.{hiring,patent,datalab}` | 각 소스 `{direction(대문자), score}` 또는 `null` |

Response (배열):

```json
[
  {
    "stock_id": 10,
    "stock": {"id": 10, "stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI"},
    "direction": "POSITIVE",
    "score": 75.0,
    "alignment_rate": 0.6,
    "source_agreement": "LOW",
    "warning_level": "WARNING",
    "data_status": "failed",
    "summary": "채용 신호 요약",
    "score_breakdown": {
      "alternative": {
        "hiring":  {"direction": "POSITIVE", "score": 80},
        "patent":  {"direction": "NEUTRAL",  "score": 50},
        "datalab": {"direction": "POSITIVE", "score": 95}
      },
      "dart": null,
      "report": null
    }
  }
]
```

> **direction 대소문자 주의:** 본 list 엔드포인트는 프론트(#335)의 `Direction`(대문자) 정합을 위해
> **대문자**(`POSITIVE`/`NEGATIVE`/`NEUTRAL`/`MIXED`)로 반환한다. 반면 아래 상세(`GET /api/signals/{signal_id}`)와
> dashboard 예시는 현재 **소문자**(`final_signals.signal` 원형) — 향후 통일 대상.
> 이 엔드포인트가 스펙상 "최신 시그널 목록(`/api/signals/latest`)" 역할을 대신한다.

### `GET /api/signals/by-stock/{stock_code}` (구현됨, 2026-06-23)

종목별 최신 published 시그널 요약을 반환한다. 인증 필요.
기존 `GET /signals/{ticker}`는 호환 라우트로 유지한다.

Response 주요 필드:

```json
{
  "signal_id": 100,
  "stock": {
    "id": 10,
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "market": "KOSPI"
  },
  "direction": "neutral",
  "score": 50,
  "alignment_rate": 0.5,
  "source_agreement": "LOW",
  "warning_level": "CAUTION",
  "data_status": "partial",
  "needs_review": true,
  "summary": "DART 데이터 방향성은 중립입니다.",
  "updated_at": "2026-06-23T00:00:00+09:00",
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

### `GET /api/signals/{signal_id}`

시그널 상세를 반환한다.

Response 주요 필드:

```json
{
  "signal_id": 100,
  "stock": {
    "stock_code": "005930",
    "stock_name": "삼성전자"
  },
  "direction": "positive",
  "score": 72,
  "alignment_rate": 0.8,
  "data_status": "partial",
  "needs_review": true,
  "summary": "여러 데이터 소스에서 유사한 방향성이 관찰되지만 일부 source는 추가 확인이 필요합니다.",
  "positive_evidence": [],
  "caution_evidence": [],
  "sources": [
    {
      "source": "DART",
      "direction": "neutral",
      "score": 50,
      "data_status": "ok",
      "summary": "공식 공시 이벤트가 확인되었습니다.",
      "evidence": []
    }
  ],
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

---

## 10. Journal API

Signal Journal은 사용자 복기 도구다. 투자 행동 추천이나 성과 평가처럼 표현하지 않는다.

### `user_view`

| 값 | 화면 표시 | 의미 |
|---|---|---|
| `watch` | 계속 관찰 | 이 데이터 방향성을 계속 관찰 대상으로 둔다 |
| `research_more` | 추가 확인 필요 | 더 확인할 근거가 필요하다 |
| `not_relevant` | 낮은 관련도 | 현재 사용자 관심 기준에서는 중요도가 낮다 |

사용하지 않는 값:

```text
buy
sell
hold
entry
exit
target_price
```

DB 반영 상태:

- `018_signal_journal_mvp_policy.sql`에서 `signal_journals.user_view` CHECK를
  `watch`, `research_more`, `not_relevant`로 변경한다.
- 기존 `bullish`, `bearish`, `neutral` 값은 migration에서 `watch`로 정리한다.
- `signal_journals.tags JSONB NOT NULL DEFAULT '[]'`를 추가한다.

### `GET /api/journals`

Query 후보:

```text
stock_code=005930
limit=20
```

### `POST /api/journals`

Request:

```json
{
  "stock_code": "005930",
  "signal_id": 100,
  "user_view": "research_more",
  "memo": "DART 공시는 확인했지만 Report 데이터가 아직 없어 추가 확인이 필요함.",
  "tags": ["DART", "추가확인"]
}
```

Response:

```json
{
  "journal_id": 20,
  "stock_code": "005930",
  "signal_id": 100,
  "user_view": "research_more",
  "memo": "DART 공시는 확인했지만 Report 데이터가 아직 없어 추가 확인이 필요함.",
  "created_at": "2026-06-17T10:00:00+09:00"
}
```

### `GET /api/journals/{journal_id}`

사용자 본인의 저널만 조회할 수 있다.

### `PATCH /api/journals/{journal_id}`

수정 가능 필드:

- `user_view`
- `memo`
- `tags`

### `DELETE /api/journals/{journal_id}`

MVP에서는 hard delete 또는 soft delete 중 하나를 선택해야 한다. 현재 `signal_journals`에는 `deleted_at`이 없으므로 hard delete가 단순하다. 감사/복기 이력을 보존하려면 후속 migration으로 `deleted_at`을 추가한다.

---

## 11. Read State API

읽음 상태는 대시보드/상세 화면의 사용성을 위한 사용자별 상태 기록이다.

```text
POST /api/signals/{signal_id}/read
```

동작:

- `user_signal_reads`에 upsert한다.
- 상세 화면 진입 시 자동 호출하거나 명시 버튼으로 호출한다.

Response:

```json
{
  "status": "read",
  "signal_id": 100,
  "read_at": "2026-06-23T00:00:00+09:00",
  "read_date": "2026-06-23",
  "notice": "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다."
}
```

---

## 12. Worker 연동 정책

Main Server는 agent-worker를 직접 대체하지 않는다.

`POST /api/signals/run/{stock_code}`에서 가능한 worker 호출 방식:

1. agent-worker queue enqueue API 호출
2. DART/PRICE/REPORT/ALTERNATIVE 작업 등록
3. `analysis_requests`와 내부 queue task id를 연결할 수 있는 메타데이터 저장

MVP에서는 source별 수집/분석 주기가 다르므로, `job.status=partial`을 허용한다.

예:

```text
DART completed
PRICE completed
REPORT missing
ALTERNATIVE missing
=> job.status = partial
=> dashboard에는 data_status="partial" 또는 source별 "missing" 노출
```

worker 실패 시:

- 기존 최신 시그널이 있으면 fallback으로 반환
- 없으면 `data_status="failed"`와 명확한 오류 메시지 반환
- 재시도 가능 여부는 내부 로그/상태에 남긴다

---

## 13. DB 변경 요약

필수 변경:

| 변경 | 이유 |
|---|---|
| `user_sessions` 추가 | refresh token 서버 세션 관리 |
| `signal_journals.user_view` CHECK 변경 | `watch`, `research_more`, `not_relevant` 사용. `018_signal_journal_mvp_policy.sql` 반영 |
| 무료/MVP 플랜 `max_watchlist=10` 반영 | 관심종목 최대 10개 정책 |
| watchlist 기본 알림 false 반영 | MVP 알림 비활성 정책 |
| `signal_journals.tags JSONB` 추가 | 저널 태그 저장. `018_signal_journal_mvp_policy.sql` 반영 |

검토 변경:

| 변경 | 이유 |
|---|---|
| `analysis_requests.status` CHECK 확장 | `queued`, `partial`, `cancelled` 표현 |
| `signal_journals.deleted_at` 추가 | 저널 soft delete가 필요하면 추가 |

이미 있는 테이블:

- `users`
- `social_accounts`
- `watchlists`
- `signal_journals`
- `user_signal_reads`
- `analysis_requests`
- `final_signals`

---

## 14. 구현 우선순위

### Phase 1 — Auth

1. `user_sessions` migration
2. password hashing/token utility
3. `POST /api/auth/signup`
4. `POST /api/auth/login`
5. `POST /api/auth/refresh`
6. `POST /api/auth/logout`
7. `GET /api/users/me`

### Phase 2 — Watchlist + Stock

1. stock search
2. watchlist add/list/delete
3. watchlist limit 10 검증
4. auth dependency 적용

### Phase 3 — Dashboard + Signal Read

1. dashboard response assembler
2. latest signal by watchlist
3. source summary missing 처리
4. signal detail
5. read state - 구현됨

### Phase 4 — Analysis Job

1. signal run request
2. analysis job status
3. agent-worker enqueue client
4. fallback/partial 처리

### Phase 5 — Journal

1. journal create/list/detail
2. journal patch/delete
3. `user_view` 정책 적용

---

## 15. 테스트 기준

Auth:

- 회원가입 성공
- `agreed_risk=false` 거부
- 중복 email 거부
- 로그인 성공/실패
- refresh token 재발급
- logout 후 refresh 거부

Watchlist:

- 로그인 필요
- 종목 추가
- 중복 추가 거부 또는 idempotent 처리
- 10개 초과 거부
- 삭제 후 목록에서 제외

Dashboard/Signal:

- 관심종목 없는 사용자 응답
- 최신 시그널 있는 종목 응답
- source별 missing 상태 응답
- `confidence`, 추천 문구 미노출

Job:

- run 요청 시 job 생성
- worker 실패 시 fallback/failed 응답
- job status 조회 권한 검증

Journal:

- 저널 작성
- `buy/sell/hold` 같은 금지 user_view 거부
- 본인 저널만 조회/수정/삭제 가능

---

## 16. 현재 구현 상태

현재 `services/main-server`에 구현된 API:

- `GET /health`
- `GET /signals/{ticker}` (호환 라우트 — `by-stock` 전신)
- `GET /api/signals` (목록, 종목당 1개 그룹핑 — 2026-06-22)
- `GET /api/signals/by-stock/{stock_code}` (종목별 최신 시그널 요약 — 2026-06-23)
- `GET /api/signals/{signal_id}` (상세 — #333)
- auth/users/stocks/watchlists/dashboard 라우터 등록됨(`app/main.py`)

이번 스펙에서 확정한 canonical API는 `/api/...` prefix를 사용한다. 기존 `GET /signals/{ticker}`는 호환 라우트로 유지한다.

