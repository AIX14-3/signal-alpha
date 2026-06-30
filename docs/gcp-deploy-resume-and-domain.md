# GCP 배포 재개 + 가비아 도메인(www/api) 연결 — GKE 마스터 런북

> 목표: 진행 중이던 **E트랙(GKE + Argo CD + Cloud SQL ×2)** 배포를 **Cloud SQL 생성 지점부터 재개**해
> 끝까지 올리고, 가비아 도메인을 **GKE Ingress(글로벌 고정 IP)** 에 **HTTPS·CORS** 까지 연결한다.
>
> - 이 문서는 **새 Cloud Shell 에서 0~10 을 복붙**하면 되도록 자족적으로 썼다(변수 휘발 → 매 세션 §0 재실행).
> - 인프라 세부는 다음을 참조(중복 최소화):
>   - 이미지 빌드: [`../deploy/README.md`](../deploy/README.md) §1
>   - Cloud SQL 오버레이·Secret·Argo: [`../deploy/overlays/cloudsql/README.md`](../deploy/overlays/cloudsql/README.md)
>   - **CORS·쿠키·소셜/결제 도메인**(플랫폼 무관): [`gabia-domain-cors-setup.md`](gabia-domain-cors-setup.md) §3·§4·§7
> - 실행은 사용자가 직접(`!`). 매니페스트는 PR 로 반영(머지는 담당자).

## 플랫폼: GKE (팀 CORS 문서는 Cloud Run 기준 — 아래만 다름)

[`gabia-domain-cors-setup.md`](gabia-domain-cors-setup.md) 는 **Cloud Run** 기준이다. **도메인 구조(www/api 서브도메인)·
CORS·쿠키·FE 재빌드·OAuth/PortOne 갱신은 그대로 채택**하되, **플랫폼 종속 부분만 GKE 로 바꾼다**:

| 항목 | 팀 문서(Cloud Run) | **이 문서(GKE)** |
|---|---|---|
| DNS | `www`·`api` **CNAME → `ghs.googlehosted.com`** | `www`·`api` **A 레코드 → 글로벌 고정 IP `sa-ingress-ip`** |
| HTTPS | Cloud Run 도메인 매핑 자동 인증서 | **GKE `ManagedCertificate`**(`deploy/k8s/managed-cert.yaml`, www+api) |
| 라우팅 | 서비스별 `gcloud run domain-mappings` | **Ingress 멀티호스트**(`deploy/k8s/ingress.yaml`: www→web / api→main-server) |
| BE env 주입 | `gcloud run services update --update-env-vars` | **ConfigMap(cloudsql 오버레이 패치) + Secret(AUTH_SECRET_KEY)** |

> `signal-alpha.cloud` 를 적용할 곳: ingress host ×2 · managed-cert domains ×2 · cloudsql 오버레이 CORS/COOKIE ·
> web build-arg(**api** 서브도메인) · OAuth/PortOne 콘솔. **전부 같은 상위도메인이어야** 인증서·CORS·쿠키가 맞물린다.

---

## 0. 변수 재export (매 Cloud Shell 세션 처음)
```bash
export PROJECT=signal-alpha-demo REGION=asia-northeast3 ZONE=asia-northeast3-a VPC=sa-vpc
export AR=$REGION-docker.pkg.dev/$PROJECT/signal-alpha
export DOMAIN=signal-alpha.cloud      # www/api 는 서브도메인
gcloud config set project $PROJECT
```

## 1. Cloud SQL ×2 생성 (멈춘 지점 — `--edition=ENTERPRISE` 추가)
기본 에디션이 `ENTERPRISE_PLUS` 라 `db-g1-small` 이 거부됐던 문제 → `--edition=ENTERPRISE` 로 해결.
```bash
for INST in sa-pg sa-be; do
  gcloud sql instances create $INST --database-version=POSTGRES_16 --edition=ENTERPRISE \
    --tier=db-g1-small --region=$REGION --storage-size=10GB --storage-auto-increase \
    --network=projects/$PROJECT/global/networks/$VPC --no-assign-ip --backup
done
# 비번은 데모 전용 새 값(서로 다르게). 평문 노출 주의.
gcloud sql users set-password postgres --instance=sa-pg --password='<NEW_PW_PG>'
gcloud sql databases create signal_alpha --instance=sa-pg
gcloud sql users set-password postgres --instance=sa-be --password='<NEW_PW_BE>'
gcloud sql databases create signal_alpha --instance=sa-be
# Private IP (시크릿 DSN host)
export PG_IP=$(gcloud sql instances describe sa-pg --format='value(ipAddresses[0].ipAddress)')
export BE_IP=$(gcloud sql instances describe sa-be --format='value(ipAddresses[0].ipAddress)')
echo "PG_IP=$PG_IP  BE_IP=$BE_IP"     # 둘 다 나오면 1단계 완료
```

## 2. GKE 클러스터 (같은 VPC, VPC-native)
Pod 가 Cloud SQL **Private IP** 로 직접 접속(사이드카 불필요).
```bash
gcloud container clusters create sa-gke --zone=$ZONE \
  --network=$VPC --subnetwork=$VPC --enable-ip-alias \
  --num-nodes=1 --machine-type=e2-medium
gcloud container clusters get-credentials sa-gke --zone=$ZONE
```

## 3. 이미지 빌드/푸시 (web 은 api 서브도메인 구워넣기)
전체는 [`../deploy/README.md`](../deploy/README.md) §1. web 만 build-arg 가 커스텀 도메인:
```bash
docker build -f web/Dockerfile --target runner \
  --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.$DOMAIN \
  -t $AR/web:demo ./web && docker push $AR/web:demo
# agent-worker / main-server / db-migrate / hiring-crawler 도 빌드·푸시(README §1)
# → kustomization 의 PROJECT_ID/newTag(또는 cloudsql 오버레이 images) 핀
```
> ⚠️ FE 재빌드 함정: 도메인 바뀌면 **반드시 재빌드**(런타임 env 로는 API 주소 안 바뀜). build-arg 는 **api** 서브도메인.
> 소셜/PortOne 까지 쓰면 해당 `NEXT_PUBLIC_*` build-arg 도 함께(아니면 mock 모드).

## 4. 글로벌 고정 IP 예약 (★ www·api A레코드가 가리킬 값)
```bash
gcloud compute addresses create sa-ingress-ip --global   # ingress.yaml annotation 이름과 일치
export ING_IP=$(gcloud compute addresses describe sa-ingress-ip --global --format='value(address)')
echo "가비아 A레코드 값 = $ING_IP"
```

## 5. 네임스페이스 + 시크릿 (Cloud SQL DSN + AUTH_SECRET_KEY)
[`../deploy/overlays/cloudsql/README.md`](../deploy/overlays/cloudsql/README.md) 의 secret 블록 그대로
(host=`$PG_IP`/`$BE_IP`, `?sslmode=require`) + **AUTH_SECRET_KEY 추가**:
```bash
kubectl create namespace signal-alpha 2>/dev/null || true
kubectl -n signal-alpha create secret generic signal-alpha-secrets \
  --from-literal=WORKER_DATABASE_URL="postgresql://signal_alpha:<PW_PG>@$PG_IP:5432/signal_alpha?sslmode=require" \
  --from-literal=BACKEND_DATABASE_URL="postgresql://signal_alpha:<PW_BE>@$BE_IP:5432/signal_alpha?sslmode=require" \
  --from-literal=MIGRATE_DATABASE_URL="postgresql://signal_alpha:<PW_PG>@$PG_IP:5432/signal_alpha?sslmode=require" \
  --from-literal=BACKEND_MIGRATE_DATABASE_URL="postgresql://signal_alpha:<PW_BE>@$BE_IP:5432/signal_alpha?sslmode=require" \
  --from-literal=AUTH_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=GEMINI_API_KEY='...' --from-literal=DART_API_KEY='...' \
  --from-literal=KIWOOM_APP_KEY='...' --from-literal=KIWOOM_APP_SECRET='...' \
  --from-literal=INTERNAL_API_TOKEN='...'
```
> `AUTH_SECRET_KEY` 는 `dev-` 접두 금지(G5 가드). CORS/쿠키 env(APP_ENV·CORS_ALLOW_ORIGINS·COOKIE_*)는
> 비밀이 아니라 **cloudsql 오버레이 ConfigMap 패치**에 이미 들어 있다(§A 매니페스트, `signal-alpha.cloud` 를 실제 도메인으로).

## 6. Argo CD + Application (cloudsql 오버레이 = 관리형 Cloud SQL)
```bash
kubectl apply -f deploy/argocd/project.yaml -f deploy/overlays/cloudsql/application.yaml
```
- `application.yaml` 의 `kustomize.images` 를 실제 `$AR` 이미지로 덮고, `targetRevision` 은 브랜치(병합 전)/`main`(병합 후).
- gyu in-cluster `application.yaml` 과 충돌 → **택1**.
- 동기화 wave: 마이그 Job ×2(1) → 앱(2) → **Ingress + ManagedCertificate(3)**.

## 7. 가비아 DNS (도메인 → 고정 IP) — TTL 짧게
가비아 → My가비아 → DNS 관리, **TTL 300**:

| 호스트 | 타입 | 값 |
|---|---|---|
| `www` | A | `$ING_IP` |
| `api` | A | `$ING_IP` |
| `@`(apex, 선택) | A → `$ING_IP` (+ ingress/cert/CORS 에 apex 추가) **또는** 가비아 웹포워딩 `@`→`https://www.signal-alpha.cloud` 301 |

> ⚠️ Cloud Run 의 `CNAME → ghs.googlehosted.com` 은 **쓰지 않는다**(그건 Cloud Run 전용). GKE 는 **A 레코드 → 고정 IP**.
> apex(`@`)는 CNAME 불가지만 A 레코드는 가능 — 직접 서빙하려면 ingress host·cert domains·CORS 에 apex 도 추가.

## 8. HTTPS 인증서 자동 발급 (DNS 전파 후)
DNS 가 `$ING_IP` 로 전파되면 ManagedCertificate 가 자동 Provision:
```bash
kubectl -n signal-alpha describe managedcertificate signal-alpha-cert
# Status: Active  → 발급 완료(보통 15~60분). www·api 둘 다 Active 여야 함.
# FailedNotVisible → DNS 전파 대기(정상). Provisioning → 발급 중.
```

## 9. OAuth / PortOne 콘솔 갱신 (로그인·결제 콜백)
naver/google/kakao **redirect_uri** + PortOne **허용 도메인** 을 `https://www.$DOMAIN` 으로 갱신.
([`gabia-domain-cors-setup.md`](gabia-domain-cors-setup.md) §4-2)

## 10. 검증
```bash
kubectl -n signal-alpha get ingress signal-alpha          # ADDRESS = $ING_IP
nslookup www.$DOMAIN && nslookup api.$DOMAIN               # $ING_IP 나오면 전파 완료
curl -I https://api.$DOMAIN/health                         # 200 + 유효 인증서
# CORS preflight — Allow-Origin 이 FE origin 과 정확히 일치하는지
curl -i -X OPTIONS https://api.$DOMAIN/api/users/me \
  -H "Origin: https://www.$DOMAIN" -H "Access-Control-Request-Method: GET"
#   기대: Access-Control-Allow-Origin: https://www.$DOMAIN  /  Access-Control-Allow-Credentials: true
```
브라우저 `https://www.$DOMAIN`: 화면+자물쇠, 로그인 후 `sa_refresh` 쿠키 **Domain=`.$DOMAIN`·Secure·SameSite=Lax**,
**새로고침해도 로그인 유지**. 데이터는 발행 스모크로 판정:
**백엔드 DSN 으로 `SELECT count(*) FROM api.signals_current;` ≥ 1** (0이면 워커 `BACKEND_DATABASE_URL` 미주입 의심).

---

## 트러블슈팅 (요약 — 상세는 팀 문서 §9)
| 증상 | 점검 |
|------|------|
| main-server 리비전 CrashLoop | 로그 `Invalid production configuration` → G5/G6: `AUTH_SECRET_KEY`(dev- 금지)·`CORS_ALLOW_ORIGINS`(빈값/`*` 금지)·SameSite=none↔Secure |
| CORS 에러 / Allow-Origin 없음 | `CORS_ALLOW_ORIGINS`(오버레이 ConfigMap)가 `https://www.signal-alpha.cloud` 와 **정확히** 일치(스킴·끝슬래시·철자) |
| 로그인 풀림 / 새로고침 로그아웃 | ① `COOKIE_DOMAIN` 앞 점(`.signal-alpha.cloud`) ② Secure/SameSite 짝 ③ **FE 가 옛 주소 호출**(§3 재빌드 누락) |
| 인증서 pending | DNS 전파 + ManagedCertificate Provision 대기. `describe managedcertificate` 상태 확인 |
| 화면은 뜨는데 데이터 빔 | CORS 무관 — 워커 `BACKEND_DATABASE_URL` 미주입(발행 no-op) |
