# 지정학 리스크 뉴스 감시 에이전트 + LLM 판정 상세 문서 (Phase 4) — 단독 라인

> ⚠️ **설계 원칙: 단독(Standalone) 베이스라인.**
> 이 기능은 **기존 테이블·기존 코드를 재사용하지 않는 독립 코드 라인**으로 만듭니다.
> - DART LLM(`app/analyzers/dart/llm.py`)을 **공유하지 않음** — 자체 LLM 클라이언트를 따로 둠.
> - 기존 분석/수집 코드와 **import·테이블을 겹치지 않음** — 전용 패키지·전용 테이블.
> - 단, **기존 컬렉터/워커 프로세스(agent-worker) 라인 위에서 실행(호스팅)하는 것은 허용** — 데몬/스케줄 인프라만 빌려 씀.
>
> 관련: 차단 동작 `docs/geo-risk-block-detail.md`, 요약 `docs/geo-risk-block-summary.md`,
> 전체 계획 `.claude/plans/https-github-com-aix14-3-signal-alpha-foamy-blossom.md`.

---

## 0. 왜 단독 라인인가

홈페이지 통제(차단)는 **서비스의 안전장치**입니다. 기존 분석 파이프라인과 코드/데이터를 얽으면:
- 기존 코드 변경이 통제 기능을 깨거나, 통제 기능 변경이 기존 분석을 깰 위험이 생깁니다.
- DART LLM 등과 공유하면 모델·프롬프트·스키마가 서로 끌려다닙니다.

→ 그래서 **통제 라인은 독립**으로 둡니다. 자체 테이블·자체 LLM 클라이언트·자체 프롬프트·자체 API.
기존 시스템과의 접점은 **"agent-worker 프로세스 안에서 같이 돈다"는 것뿐**입니다(코드는 분리).

---

## 1. 전용 패키지 구조 (다른 코드와 겹치지 않음)

모든 통제 라인 코드는 **단일 전용 패키지** 안에 모읍니다. 외부 모듈을 import 하지 않습니다.

```
services/agent-worker/app/homepage_guard/        ← 통제 라인 전용 패키지 (신규, 독립)
├── __init__.py
├── config.py            # 전용 설정 (기존 app/core/config.py 와 분리)
├── llm_client.py        # 전용 LLM 클라이언트 (dart/llm.py 미사용, 자체 구현)
├── news_collector.py    # 전용 뉴스 수집기 (기존 collectors/ 미사용)
├── risk_judge.py        # 전용 LLM 판정·검증
├── gate.py              # 차단 결정 로직 (advisory/auto)
├── repository.py        # 전용 테이블 전담 DB 접근 (기존 repositories/ 미사용)
├── daemon.py            # 주기 실행 루프
└── prompts/
    └── geo_risk_v1.md   # 전용 프롬프트 (dart 프롬프트 미사용)
```

> 규칙: `homepage_guard/` 안의 코드는 **`app.analyzers.*`, `app.collectors.*`,
> `packages/data-access/*` 를 import 하지 않습니다.** 필요한 것은 패키지 내부에 자체 구현합니다.
> (psycopg/httpx 같은 범용 라이브러리는 사용 가능)

호스팅만 공유: `app/main.py` lifespan에서 `homepage_guard.daemon` 을 기동(또는 전용 크론 엔드포인트).
원하면 추후 **별도 마이크로서비스로 분리**하기도 쉬움(이미 독립 패키지라 그대로 떼면 됨).

---

## 2. 파이프라인 (전부 전용 코드)

```
 [뉴스 소스]        [news_collector]        [risk_judge + llm_client]      [gate]
 GDELT 등   ──▶  자체 수집·중복제거  ──▶  자체 LLM 호출·검증(JSON)  ──▶  차단 제안/자동차단
                  guard_news_events 저장      severity 산출                 guard_site_status 갱신
```

기존 `collect→normalize→analyze` 큐 태스크에 **끼어들지 않습니다.** 통제 라인은 자기 루프로 돕니다.

---

## 3. 뉴스 소스

| 소스 | 비용/키 | 특징 | 추천 |
|------|---------|------|------|
| **GDELT** | 무료 / 키 불필요 | 전 세계 뉴스 이벤트·톤 지표, 지정학에 특화, 15분 갱신 | ★ 시작점 |
| NewsAPI | 무료한도+유료 / 키 필요 | 헤드라인 검색 | 보조 |
| 네이버 뉴스 검색 | 무료한도 / 키 필요 | 한국어 커버리지 | 보조 |

수집·HTTP 호출은 `news_collector.py` 안에서 **자체 구현**(기존 수집기 코드 미사용).
키워드는 전용 설정값으로 관리: `war, ceasefire, sanction, strike, conflict, 이란, 미국, 휴전, 확전`.

---

## 4. 전용 LLM 클라이언트 (`homepage_guard/llm_client.py`)

DART의 `OpenAiChatClient`/`GeminiGenerateContentClient` 를 **import 하지 않고**, 같은 개념을
**이 패키지 안에 독립적으로 다시 구현**합니다. (코드 공유 없음, 스키마·프롬프트도 독립)

- 인터페이스(자체 정의):
  ```python
  class GuardLlmClient(Protocol):
      async def complete(self, *, prompt: str, model: str, timeout_seconds: float) -> str: ...
  ```
- 구현체(전용): `GuardOpenAiClient`, `GuardGeminiClient`
  - JSON 강제 출력: OpenAI `response_format={"type":"json_object"}` / Gemini `response_mime_type="application/json"`
  - `temperature=0` 으로 결정적 판정
  - HTTP는 패키지 내부에서 직접 호출(자체 에러 타입 `GuardLlmError`)
- 프로바이더 선택: 전용 설정 `GUARD_LLM_PROVIDER = gemini | openai`
- (선택) Anthropic을 쓰려면 동일 `GuardLlmClient` 프로토콜로 `GuardAnthropicClient` 를 패키지 안에 추가.
  현재 레포에 Anthropic 클라이언트는 없음.

> 같은 패턴을 "다시 구현"하는 것이 중복처럼 보일 수 있지만, **의도된 독립**입니다.
> 통제 라인의 모델/프롬프트/스키마가 DART 분석과 무관하게 따로 진화하도록 보장합니다.

---

## 5. LLM 판정 (`homepage_guard/risk_judge.py`)

### 5-1. 프롬프트 (전용 템플릿)
- 위치: `app/homepage_guard/prompts/geo_risk_v1.md` (DART 프롬프트 미사용, 별도 파일)
- 코드가 기사 묶음을 `{{INPUT_JSON}}` 자리에 끼워 넣음.
- 버전 태깅: 패키지 상수 `GUARD_PROMPT_VERSION = "geo-risk-v1"`.

**프롬프트 요지**
```
당신은 지정학 리스크 분석가입니다. 아래 뉴스 묶음을 읽고, 한국 주식시장에 미칠
지정학적 충격을 평가해 "엄격한 JSON"으로만 답하세요.
규칙:
- 투자 조언(매수/매도/보유/목표가)을 절대 쓰지 마세요.
- 근거가 약하면 confidence를 낮추고 severity를 보수적으로.
- 한국 시장과 무관하면 is_geopolitical_risk=false.
출력 스키마: { severity, is_geopolitical_risk, direction, summary, regions, affected_themes, confidence, evidence }
입력:
{{INPUT_JSON}}
```
- few-shot 2~3개(확전→고severity / 휴전→deescalation / 무관→false)를 템플릿에 포함.

### 5-2. 출력 JSON 스키마 (통제 라인 전용 계약)
| 필드 | 타입 | 범위/값 | 의미 |
|------|------|---------|------|
| `severity` | int | 0~100 | 한국 시장 충격 크기 |
| `is_geopolitical_risk` | bool | true/false | 지정학 리스크 여부 |
| `direction` | string | `escalation`/`deescalation`/`unclear` | 확전·완화·불명확 |
| `summary` | string | 1~2문장 | 차단 화면 사유 후보 |
| `regions` | string[] | 예 ["Iran","US"] | 관련 지역 |
| `affected_themes` | string[] | 예 ["oil","defense"] | 영향 테마 |
| `confidence` | int | 0~100 | 확신도 |
| `evidence` | string[] | 근거 문장 | 판단 근거 |

**예시**
```json
{
  "severity": 88, "is_geopolitical_risk": true, "direction": "escalation",
  "summary": "이란-미국 무력 충돌 확전, 호르무즈 해협 봉쇄 우려.",
  "regions": ["Middle East","Iran","US"], "affected_themes": ["oil","defense","shipping"],
  "confidence": 76, "evidence": ["미군 기지 추가 타격 발표","유가 9% 급등"]
}
```

### 5-3. 파싱·검증 (전용 헬퍼, 자체 구현)
DART의 검증 헬퍼를 **import 하지 않고** 패키지 안에 자체 작성:
1. 코드펜스(```json) 제거 후 dict 파싱 (실패 시 `GuardLlmError`)
2. `severity`/`confidence` → 0~100 범위 강제
3. `direction` → 허용집합 외면 거부
4. `regions`/`affected_themes`/`evidence` → 문자열 리스트 정규화
5. `summary` → 필수 문자열
6. `summary`/`evidence` → 투자조언 단어(매수/매도/보유/목표가/buy/sell/hold) 차단(자체 가드레일)
7. 통과분만 전용 dataclass `GeoRiskJudgment` 로 반환

> 핵심: LLM이 뭘 뱉든 **통제 라인 코드가 형식·범위·금지어를 강제**. LLM은 제안, 결정은 코드.

---

## 6. 차단 결정 (`homepage_guard/gate.py`) → 전용 상태 테이블 갱신

판정 결과를 받아 `mode`별로 처리하고, **전용 상태 테이블 `guard_site_status`** 만 갱신합니다.
(기존 `final_signals.is_published` 등 기존 테이블/컬럼은 건드리지 않음)

```
조건: is_geopolitical_risk == true  AND  severity >= GUARD_SEVERITY_THRESHOLD

mode:
  advisory → guard_site_status 그대로, guard_recommendations 에 "제안" 적재 + 알림 (사람 승인)
  auto     → guard_site_status 를 blocked 로 설정(scope, reason=summary,
             triggered_by='agent:geo-risk-monitor'); auto scope 상한·쿨다운 적용; 완화 시 자동 해제
  manual   → 무시(관리자만)
```

- **severity→scope 매핑(예시)**: 50~69 `report_generation`, 70~89 `report_view`, 90~100 `whole_site`(auto는 자동실행 금지·승인 필요).

---

## 7. 전용 테이블 (기존 테이블 미사용)

전용 마이그레이션으로 **통제 라인 전용 테이블만** 추가합니다. 접두사 `guard_` 로 네임스페이스 분리.

```sql
-- 차단 스위치(단일 행) = 통제의 단일 진실원천
CREATE TABLE guard_site_status (
    id           SMALLINT PRIMARY KEY DEFAULT 1,
    status       VARCHAR(20) NOT NULL DEFAULT 'ok',     -- ok | blocked
    scope        VARCHAR(30) NOT NULL DEFAULT 'report_generation',
    mode         VARCHAR(20) NOT NULL DEFAULT 'manual', -- manual | advisory | auto
    reason       TEXT,
    resume_at    TIMESTAMPTZ,
    triggered_by VARCHAR(100),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT guard_site_status_singleton CHECK (id = 1)
);
INSERT INTO guard_site_status (id) VALUES (1) ON CONFLICT DO NOTHING;

-- 수집·판정한 뉴스 이력
CREATE TABLE guard_news_events (
    id            BIGSERIAL PRIMARY KEY,
    source        VARCHAR(20) NOT NULL,        -- gdelt | newsapi | naver
    article_hash  VARCHAR(64) NOT NULL UNIQUE, -- 중복 제거 키
    title         TEXT, url TEXT, published_at TIMESTAMPTZ,
    severity            SMALLINT,
    is_geopolitical_risk BOOLEAN,
    direction           VARCHAR(20),
    summary             TEXT,
    regions             TEXT[], affected_themes TEXT[], confidence SMALLINT,
    prompt_version      VARCHAR(40),
    judged_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 차단 제안(advisory) / 변경 감사 로그
CREATE TABLE guard_recommendations (
    id BIGSERIAL PRIMARY KEY,
    news_event_id BIGINT REFERENCES guard_news_events(id),
    suggested_scope VARCHAR(30), severity SMALLINT, reason TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE guard_status_audit (
    id BIGSERIAL PRIMARY KEY,
    action VARCHAR(20) NOT NULL, scope VARCHAR(30), reason TEXT, actor VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
- DB 접근은 `homepage_guard/repository.py` 가 **전담**(기존 `repositories/` 미사용).
- 마이그레이션 파일은 기존 번호 체계와 충돌 안 나게 다음 번호로 추가(예: `019_homepage_guard.sql`).

---

## 8. 전용 설정 (`homepage_guard/config.py`)

기존 `app/core/config.py` 와 분리된 **전용 설정 로더**(env 직접 읽음).

| 설정 | 기본값(예) | 의미 |
|------|-----------|------|
| `GUARD_ENABLED` | `false` | 통제 라인 데몬 on/off |
| `GUARD_POLL_INTERVAL_SEC` | `900` | 폴링 주기 |
| `GUARD_NEWS_SOURCE` | `gdelt` | 뉴스 소스 |
| `GUARD_KEYWORDS` | `war,ceasefire,...` | 감시 키워드 |
| `GUARD_LLM_PROVIDER` | `gemini` | gemini/openai |
| `GUARD_LLM_MODEL` | `""` | 모델명 |
| `GUARD_LLM_TIMEOUT_SECONDS` | `20` | LLM 타임아웃 |
| `GUARD_SEVERITY_THRESHOLD` | `70` | 차단 제안/실행 임계 |
| `GUARD_AUTO_MAX_SCOPE` | `report_generation` | auto 자동차단 scope 상한 |
| `GUARD_LLM_API_KEY` | `""` | 통제 라인 전용 키(기존 키와 분리 권장) |

> LLM API 키도 가능하면 **통제 라인 전용 키**로 분리해 사용량/비용을 독립 추적.

---

## 9. 주기 실행 (`homepage_guard/daemon.py`)

- 호스팅: `app/main.py` lifespan에서 `GUARD_ENABLED=true`일 때만 `homepage_guard.daemon.run()` 기동.
  (기존 데몬들과 같은 프로세스에서 돌지만, 코드·상태는 완전 분리)
- 루프: `폴링 → 수집(중복제거) → LLM 판정·검증 → gate 결정 → guard_* 테이블 갱신`.
- 대안: 외부 크론이 전용 엔드포인트 `POST /internal/guard/monitor` 호출(역시 전용 라우터).

---

## 10. 홈페이지 통제 접점 (프론트가 보는 전용 API)

통제 라인은 **자체 공개 API** 하나만 노출하고, 프론트는 그것만 봅니다(기존 신호 API와 무관).
- `GET /api/guard/status` → `{ status, scope, reason, resume_at }`
- 관리자용: `GET/PUT /api/guard/admin/status`, `POST /api/guard/admin/recommendations/{id}/apply`
- 라우터도 전용 파일(`app/api/routes/guard.py` 또는 통제 패키지 내부)로 두어 기존 라우터와 분리.

> 프론트 차단 enforcement는 이 전용 API만 폴링해서 처리 → 기존 페이지 데이터 흐름과 겹치지 않음.
> (상세: `docs/geo-risk-block-detail.md`)

---

## 11. 안전·비용·검증 (요지)

| 항목 | 대책 |
|------|------|
| 비용 | dedupe + 키워드/톤 1차 필터로 LLM 호출 최소화, 전용 키로 비용 격리 |
| 환각 | temperature=0, 엄격 JSON, 코드단 검증, confidence 낮으면 보수적 |
| 오탐 자동차단 | 기본 advisory(사람 승인), auto는 `GUARD_AUTO_MAX_SCOPE` 상한+쿨다운 |
| 투자조언 혼입 | 전용 가드레일로 금지어 차단 |
| 소스/LLM 장애 | 실패 시 상태 변경 없음(기존 상태 유지) |
| 회귀 추적 | `GUARD_PROMPT_VERSION` 태깅 |

**검증 순서**: ① 수집 단독 → ② 판정 단독(확전/휴전/무관 3샘플 + 잘못된 JSON 거부) →
③ 임계값/연동(advisory 제안만 vs auto 자동차단·상한·쿨다운) → ④ 데몬 end-to-end.

---

## 12. 구현 순서 (권장)

1. 전용 패키지 뼈대 `app/homepage_guard/` 생성(빈 모듈들)
2. 전용 마이그레이션 `019_homepage_guard.sql` (guard_* 테이블)
3. `config.py`(전용 설정) → `repository.py`(전용 DB 접근)
4. `llm_client.py`(전용) + `prompts/geo_risk_v1.md` + `risk_judge.py`(판정·검증)
5. `news_collector.py`(GDELT부터)
6. `gate.py`(advisory/auto 결정) + 전용 라우터 `/api/guard/*`
7. `daemon.py` → `app/main.py` lifespan 등록(또는 크론 엔드포인트)
8. 프론트 통제 접점 연결(`/api/guard/status` 폴링)

> 통제 라인은 **처음부터 독립**이라, 나중에 별도 서비스로 떼내기 쉬움.
> Phase 1~3(수동 차단)은 이 라인의 `guard_site_status` + 전용 API + 프론트만으로 이미 완성됩니다.
