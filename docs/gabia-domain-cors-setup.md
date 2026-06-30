# 가비아 도메인 + GCP 배포 — CORS / 도메인 설정 가이드

> 목표: 가비아에서 구매한 도메인을 GCP에 배포한 signal-alpha(FE/BE/DB/Worker)에 연결하고,
> 브라우저 ↔ 백엔드 사이의 **CORS·쿠키**가 정상 동작하도록 컴포넌트별로 무엇을 설정해야 하는지 정리한다.
>
> 이 문서는 전체 배포 절차서 [`gcp-deploy-runbook.md`](gcp-deploy-runbook.md) 를 **대체하지 않는다**.
> 런북은 `*.run.app` 기본 URL 기준이고, 이 문서는 거기에 **커스텀 도메인 + CORS** 부분만 덧붙인다.
> 인프라 생성(Cloud SQL ×2, VPC, 이미지 빌드 등)은 런북을 먼저 따른다.

## 채택한 도메인 구조 — 같은 상위도메인의 서브도메인 분리

```
FE  →  https://www.<도메인>     (예: https://www.example.com)
BE  →  https://api.<도메인>     (예: https://api.example.com)
```

이 구조를 쓰는 이유:
- FE와 BE가 **같은 등록가능 도메인(registrable domain)** 을 공유 → 쿠키를 `COOKIE_DOMAIN=.<도메인>` 으로
  공유할 수 있고, **`SameSite=Lax` 를 유지**해도 로그인 쿠키가 정상 송신된다(가장 안정적).
- 단, `www` 와 `api` 는 서로 **다른 origin** 이므로 브라우저는 여전히 CORS preflight를 건다 →
  **CORS 설정은 그대로 필요**하다.

> 본문의 `<도메인>` 은 본인 도메인(`example.com`)으로, `<PROJECT>`·`$REGION` 등은 런북의 변수로 치환한다.

---

## 1. 개요 — 어디에 CORS를 설정하는가

| 컴포넌트 | 노출 방식 | 도메인 | CORS 관여 |
|---------|----------|--------|----------|
| **FE (web)** | Cloud Run + 커스텀 도메인 | `www.<도메인>` | CORS **요청 주체** (브라우저에서 BE 호출). 재빌드 필요 |
| **BE (main-server)** | Cloud Run + 커스텀 도메인 | `api.<도메인>` | **CORS 설정 대상 — 여기 한 곳만** |
| **DB (Cloud SQL ×2)** | Private IP (VPC 내부) | 도메인 없음 | **무관** (브라우저가 직접 접근 안 함) |
| **Worker (agent-worker)** | GCE VM 내부 전용 | 도메인 없음 | **무관** (HTTP 아닌 DB 직접 발행) |

> **한 줄 요약: CORS는 BE 한 곳만 설정한다. FE는 도메인이 바뀌면 재빌드한다. DB와 Worker는 비공개라 CORS가 없다.**

---

## 2. 가비아 DNS 설정 (도메인 쪽에서 하는 일)

Cloud Run에 커스텀 도메인을 매핑하면 GCP가 등록해야 할 DNS 레코드를 알려준다. 그 값을
**가비아 → My가비아 → DNS 관리(또는 도메인 → DNS 정보 변경)** 에 등록한다.

### 2-1. Cloud Run 도메인 매핑 생성

```bash
# 사전: 도메인 소유 확인(최초 1회) → Google Search Console에서 도메인 인증 후
#       gcloud domains verify <도메인>  (안내에 따라 TXT 레코드를 가비아에 등록)

# FE
gcloud run domain-mappings create --service=sa-frontend \
  --domain=www.<도메인> --region=$REGION
# BE
gcloud run domain-mappings create --service=sa-backend \
  --domain=api.<도메인> --region=$REGION

# 등록해야 할 레코드 확인
gcloud run domain-mappings describe --domain=www.<도메인> --region=$REGION
gcloud run domain-mappings describe --domain=api.<도메인>  --region=$REGION
```

### 2-2. 가비아에 등록할 레코드

- 서브도메인(`www`, `api`)은 보통 **CNAME → `ghs.googlehosted.com`** (매핑 결과가 안내하는 값 그대로).

  | 호스트(가비아) | 타입 | 값 |
  |---------------|------|-----|
  | `www` | CNAME | `ghs.googlehosted.com.` |
  | `api` | CNAME | `ghs.googlehosted.com.` |

  > 매핑 `describe` 가 CNAME이 아니라 A/AAAA 레코드를 주면 그 값을 그대로 등록한다.

- **apex(`@`, 루트 도메인 `example.com`) 는 CNAME 불가** 라는 DNS 제약이 있다. 가비아도 마찬가지.
  - 권장: FE를 `www.<도메인>` 으로 쓰고, apex 접속은 가비아 **웹 포워딩(www로 301)** 으로 처리.
  - apex를 꼭 직접 서빙하려면 Cloud Run 대신 **외부 HTTPS 로드밸런서 + 고정 IP(A 레코드)** 가 필요(범위 밖, 한 줄만 언급).

### 2-3. SSL/TLS 인증서

- **가비아에서 SSL 인증서를 따로 구매할 필요 없다.**
- Cloud Run 도메인 매핑이 **Google 관리 인증서를 자동 발급/갱신**한다.
- DNS 전파 후 인증서가 `Active` 가 될 때까지 수 분~수십 분 걸릴 수 있다(이 동안 HTTPS가 잠깐 pending).

---

## 3. 백엔드(BE) — CORS 본체

CORS·쿠키는 **환경변수만으로** 동작한다. 코드(`services/main-server/app/main.py` 의 `CORSMiddleware`)는
손대지 않는다 — 이미 `CORS_ALLOW_ORIGINS` env를 읽고 `allow_credentials=True` 로 동작한다.

### 3-1. 프로덕션 환경변수

```bash
APP_ENV=production
CORS_ALLOW_ORIGINS=https://www.<도메인>     # 정확히. 끝 슬래시(/) 없이, https:// 스킴 포함
COOKIE_SECURE=true                          # HTTPS 필수 (Cloud Run은 항상 HTTPS라 충족)
COOKIE_SAMESITE=lax                          # 같은 상위도메인 서브도메인이라 lax로 충분
COOKIE_DOMAIN=.<도메인>                      # 맨 앞 점(.) → www·api 서브도메인이 쿠키 공유
AUTH_SECRET_KEY=<openssl rand -hex 32 로 생성한 강력한 값>   # 'dev-' 접두사 금지
```

### 3-2. 적용 명령 (Cloud Run)

```bash
gcloud run services update sa-backend --region=$REGION \
  --update-env-vars=\
APP_ENV=production,\
CORS_ALLOW_ORIGINS=https://www.<도메인>,\
COOKIE_SECURE=true,\
COOKIE_SAMESITE=lax,\
COOKIE_DOMAIN=.<도메인>
# AUTH_SECRET_KEY 는 Secret Manager 로 주입(런북 §4) — 평문 env로 두지 않는다.
```

### 3-3. 알아둘 점

- **apex와 www 둘 다** 프론트로 받을 거면 쉼표로 둘 다 등록:
  ```bash
  CORS_ALLOW_ORIGINS=https://www.<도메인>,https://<도메인>
  ```
  (`config.py` 가 쉼표로 split + strip 하므로 여러 오리진 지원.)
- **G5/G6 부팅 가드(의도된 안전장치)** — `APP_ENV=production` 이면 BE 부팅 시 검증한다
  (`services/main-server/app/core/config.py:76-94`):
  - `AUTH_SECRET_KEY` 가 비었거나 `dev-` 로 시작 → **부팅 실패**
  - `CORS_ALLOW_ORIGINS` 가 비었거나 `*` 포함 → **부팅 실패** (credentials=True와 `*`는 브라우저가 거부)
  - `COOKIE_SAMESITE=none` 인데 `COOKIE_SECURE` 가 false → **부팅 실패**

  → 잘못 설정하면 조용히 깨지지 않고 리비전이 unhealthy로 죽는다. 로그의 `Invalid production configuration` 확인.

---

## 4. 프론트엔드(FE) — 도메인 바뀌면 **재빌드 필수**

FE가 BE를 부르는 주소는 `NEXT_PUBLIC_MAIN_API_BASE_URL` 인데, Next.js의 `NEXT_PUBLIC_*` 는
**빌드타임에 클라이언트 번들로 인라인**된다(`web/src/lib/apiClient.ts:6`). 즉:

> ⚠️ **Cloud Run의 런타임 env를 바꿔도 FE의 API 주소는 안 바뀐다.** 반드시 build-arg로 넣어 **다시 빌드**해야 한다.
> 이게 커스텀 도메인 전환 시 가장 흔한 함정이다(FE가 계속 옛 `*.run.app` BE를 호출해서 CORS/쿠키가 깨짐).

### 4-1. BE 주소를 넣어 재빌드 + 재배포 (런북 §9 패턴)

```bash
gcloud builds submit web/ --tag $AR/web:demo \
  --config=/dev/stdin <<'YAML'
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','--build-arg','NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.<도메인>','-t','$AR/web:demo','web']
images: ['$AR/web:demo']
YAML
gcloud run deploy sa-frontend --image=$AR/web:demo --region=$REGION --allow-unauthenticated
```

> 로컬 도커면:
> `docker build --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.<도메인> -t $AR/web:demo web/ && docker push ...`

### 4-2. 추가 작업 없음 / 함께 점검할 것

- `credentials:"include"` 는 이미 `web/src/lib/apiClient.ts:37` 에 박혀 있다 → FE 쪽 코드 변경 없음.
- **소셜 OAuth redirect_uri** (naver/google/kakao 콘솔)와 **PortOne 허용 도메인** 을 새 도메인(`https://www.<도메인>`)으로 갱신해야 로그인/결제 콜백이 깨지지 않는다.

---

## 5. DB (Cloud SQL ×2) — CORS 없음

- DB는 브라우저가 직접 접근하지 않으므로 **CORS와 무관**하다.
- 런북대로 **Private IP + VPC 커넥터** 를 유지하고 공인 IP를 부여하지 않는다.
- 도메인이 바뀌어도 DB는 영향 없다 — Secret Manager의 DSN(`BACKEND_DATABASE_URL` 등)만 정상이면 된다.
- 토폴로지: 수집 DB `sa-pg` / 백엔드 DB `sa-be` 물리 분리(런북 §1).

---

## 6. Worker (agent-worker) — CORS 없음

- 워커는 백엔드 HTTP API를 호출하지 않는다. `BACKEND_DATABASE_URL` 로 **백엔드 DB에 직접 발행 복사**한다
  (`services/agent-worker/app/publish/publish_task.py`). 브라우저와 무관 → **CORS 설정 대상이 아니다.**
- 도메인 변경이 워커 동작에 주는 영향은 없다.
- (참고) 워커의 외부 데이터 수집 egress가 필요하면 Cloud NAT를 구성하는 것은 CORS와 별개 사안이다(런북 §11).

---

## 7. 쿠키 / SameSite 의사결정

| 구조 | SameSite | Secure | COOKIE_DOMAIN | 비고 |
|------|----------|--------|---------------|------|
| **같은 상위도메인 서브도메인** (`www`/`api`) — **채택** | `lax` | `true` | `.<도메인>` | 권장. 쿠키 공유 가능, 가장 안정 |
| BE를 `*.run.app` 로 둠 (교차 사이트) | `none` | `true` | 공유 불가(비움) | `none`은 반드시 `Secure=true` 동반(G6). 일부 브라우저 서드파티 쿠키 차단에 취약 |

- 우리는 첫 번째(서브도메인 분리)를 채택했으므로 **`SameSite=Lax` + `COOKIE_DOMAIN=.<도메인>`** 가 정답이다.
- 쿠키는 `sa_refresh`(경로 `/api/auth`), `sa_admin`(경로 `/api/admin`) 두 개(`config.py:25-30`).
  `COOKIE_DOMAIN=.<도메인>` 이면 두 쿠키 모두 서브도메인 간 공유된다.

---

## 8. 배포 후 검증 체크리스트

```bash
# 1) BE 헬스 — 200
curl -I https://api.<도메인>/health

# 2) CORS preflight — 200 + Access-Control-Allow-Origin 이 FE origin과 정확히 일치하는지
curl -i -X OPTIONS https://api.<도메인>/api/users/me \
  -H "Origin: https://www.<도메인>" \
  -H "Access-Control-Request-Method: GET"
# 기대: Access-Control-Allow-Origin: https://www.<도메인>
#       Access-Control-Allow-Credentials: true
```

브라우저 DevTools에서:
- `www.<도메인>` 접속 → Network 탭에서 `api.<도메인>` 호출의 preflight(OPTIONS) 200 확인.
- 로그인 후 Application → Cookies에서 `sa_refresh` 의 **Domain=`.<도메인>`, Secure ✓, SameSite=Lax** 확인.
- **새로고침해도 로그인 유지**되는지(refresh 쿠키가 교차 서브도메인으로 송신되는지) 확인.

---

## 9. 트러블슈팅

| 증상 | 원인 / 점검 |
|------|------------|
| 응답에 `Access-Control-Allow-Origin` 없음 / CORS 에러 | `CORS_ALLOW_ORIGINS` 가 FE origin과 **정확히** 일치하는지 — 스킴(`https://`)·**끝 슬래시 없음**·서브도메인 철자 |
| 로그인이 안 풀림 / 새로고침하면 로그아웃 | ① `COOKIE_DOMAIN` 앞 점(`.<도메인>`) 누락 ② `COOKIE_SECURE`·`COOKIE_SAMESITE` 짝 ③ **FE가 아직 옛 `*.run.app` 을 호출**(§4 재빌드 누락) |
| BE 리비전이 unhealthy로 죽음 | 로그의 `Invalid production configuration` → G5/G6 가드: `AUTH_SECRET_KEY`(dev- 금지)·`CORS_ALLOW_ORIGINS`(빈값/`*` 금지)·SameSite=none↔Secure |
| HTTPS가 안 잡힘 / 인증서 pending | 가비아 DNS 전파 대기 + Cloud Run 도메인 매핑 인증서 발급 대기(수 분~수십 분). `gcloud run domain-mappings describe` 로 상태 확인 |
| 데이터는 비어 있는데 화면은 뜸 | CORS와 무관 — 워커 `BACKEND_DATABASE_URL` 미주입(발행 no-op) 의심(런북 §13) |

---

## 부록 — 빠른 설정 요약

| 컴포넌트 | 해야 할 일 |
|---------|-----------|
| **가비아 DNS** | `www`·`api` CNAME → `ghs.googlehosted.com`, 도메인 소유 TXT 인증 |
| **BE env** | `APP_ENV=production`, `CORS_ALLOW_ORIGINS=https://www.<도메인>`, `COOKIE_SECURE=true`, `COOKIE_SAMESITE=lax`, `COOKIE_DOMAIN=.<도메인>`, 강력한 `AUTH_SECRET_KEY` |
| **FE** | `NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.<도메인>` 로 **재빌드** + 재배포, OAuth/PortOne 도메인 갱신 |
| **DB** | CORS 없음. Private IP 유지 |
| **Worker** | CORS 없음. 변경 없음 |
