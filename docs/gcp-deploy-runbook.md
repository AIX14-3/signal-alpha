# GCP 배포 런북 (데모 — 직접 실행용)

> 목표: FE/BE=**Cloud Run**, Worker=**GCE VM**, DB=**Cloud SQL ×2(수집/백엔드 물리분리, #531/#525)**, 리포트=**GCS**.
> 신규 GCP 계정 **$300 크레딧(90일)** 으로 사실상 무료. 끝까지 따라 하면 브라우저에서 화면이 뜬다.
> 모든 `<...>` 는 본인 값으로 치환. 명령은 Cloud Shell 또는 `gcloud` 설치된 로컬에서 실행.
>
> **DB 토폴로지(#531 2-DB 물리 분리):** 수집(워커) DB `sa-pg` 와 백엔드(서비스) DB `sa-be` 를
> **물리적으로 다른 Cloud SQL 인스턴스 2대**로 띄운다. 마이그/시드는 `migrate.py --target` 으로
> 인스턴스별로 다른 집합을 적용한다(collection / backend). cross-DB JOIN 은 불가하므로 워커가
> 발행 산출물(PUBLISHED 6테이블)을 백엔드로 **앱레벨 복사**한다(`publish_stock`).
> 분류 규칙·검증 절차의 단일 출처 = [docs/runbooks/db-2-instance-bootstrap.md](runbooks/db-2-instance-bootstrap.md).

## 0. 변수 (한 번 정해두고 복붙)
```bash
export PROJECT=<your-gcp-project-id>
export REGION=asia-northeast3        # 서울
export ZONE=asia-northeast3-a
export AR=$REGION-docker.pkg.dev/$PROJECT/sa-images
gcloud config set project $PROJECT
gcloud services enable compute.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com servicenetworking.googleapis.com \
  storage.googleapis.com cloudbuild.googleapis.com run.googleapis.com vpcaccess.googleapis.com
```

## 1. 네트워크 + Cloud SQL ×2 (Private IP)
```bash
gcloud compute networks create sa-vpc --subnet-mode=auto
gcloud compute addresses create google-managed-services-sa-vpc \
  --global --purpose=VPC_PEERING --prefix-length=16 --network=sa-vpc
gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-sa-vpc --network=sa-vpc

# (1) 수집(워커) DB 인스턴스
gcloud sql instances create sa-pg --database-version=POSTGRES_16 --tier=db-g1-small \
  --region=$REGION --storage-size=10GB --storage-auto-increase \
  --network=projects/$PROJECT/global/networks/sa-vpc --no-assign-ip --backup
gcloud sql users set-password postgres --instance=sa-pg --password='<OWNER_PASS>'
gcloud sql databases create signal_alpha --instance=sa-pg

# (2) 백엔드(서비스) DB 인스턴스 — 물리적으로 별도
gcloud sql instances create sa-be --database-version=POSTGRES_16 --tier=db-g1-small \
  --region=$REGION --storage-size=10GB --storage-auto-increase \
  --network=projects/$PROJECT/global/networks/sa-vpc --no-assign-ip --backup
gcloud sql users set-password postgres --instance=sa-be --password='<BE_OWNER_PASS>'
gcloud sql databases create signal_alpha --instance=sa-be

# Private IP 2개 확인(이후 DSN 에 사용)
export PRIVATE_IP=$(gcloud sql instances describe sa-pg --format='value(ipAddresses[0].ipAddress)')
export BE_PRIVATE_IP=$(gcloud sql instances describe sa-be --format='value(ipAddresses[0].ipAddress)')
echo "수집 sa-pg IP = $PRIVATE_IP / 백엔드 sa-be IP = $BE_PRIVATE_IP"
```
> pgvector 는 제거됐으므로 `CREATE EXTENSION vector` 는 **하지 않는다**.
> 두 인스턴스는 같은 `sa-vpc` 안이라 하나의 VPC 커넥터(§2)로 둘 다 도달한다.

## 2. VPC 커넥터 (Cloud Run → Private IP)
```bash
gcloud compute networks vpc-access connectors create sa-conn \
  --region=$REGION --network=sa-vpc --range=10.8.0.0/28
```

## 3. GCS 리포트 버킷 + 워커 서비스계정
```bash
gcloud storage buckets create gs://signal-alpha-reports --location=$REGION
gcloud iam service-accounts create sa-worker --display-name="Signal Alpha worker"
gcloud storage buckets add-iam-policy-binding gs://signal-alpha-reports \
  --member="serviceAccount:sa-worker@$PROJECT.iam.gserviceaccount.com" --role=roles/storage.objectAdmin
```

## 4. 시크릿 (Secret Manager)
> **수집 DSN 2개**(→`sa-pg`) + **백엔드 DSN 2개**(→`sa-be`). 컷오버 전: 모두 **owner** 로 둔다(폴백).
> §7에서 worker/backend 만 제한 롤로 교체. DB 이름은 두 인스턴스 모두 `signal_alpha`.
```bash
# ── 수집(sa-pg) DSN ──
DSN_COLL="postgresql://postgres:<OWNER_PASS>@$PRIVATE_IP:5432/signal_alpha?sslmode=require"
printf '%s' "$DSN_COLL" | gcloud secrets create MIGRATE_DATABASE_URL --data-file=-   # 수집 마이그(owner)
printf '%s' "$DSN_COLL" | gcloud secrets create WORKER_DATABASE_URL  --data-file=-   # 워커 접속
# ── 백엔드(sa-be) DSN ──
DSN_BE="postgresql://postgres:<BE_OWNER_PASS>@$BE_PRIVATE_IP:5432/signal_alpha?sslmode=require"
printf '%s' "$DSN_BE" | gcloud secrets create BACKEND_DATABASE_URL          --data-file=-  # 서비스 접속 + 발행 대상
printf '%s' "$DSN_BE" | gcloud secrets create BACKEND_MIGRATE_DATABASE_URL --data-file=-  # 백엔드 마이그(owner)
# 앱 시크릿(값은 본인 것으로)
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets create AUTH_SECRET_KEY --data-file=-
printf '%s' '<GEMINI_API_KEY>'   | gcloud secrets create GEMINI_API_KEY   --data-file=-
printf '%s' '<DART_API_KEY>'     | gcloud secrets create DART_API_KEY     --data-file=-
printf '%s' '<PORTONE_API_SECRET>' | gcloud secrets create PORTONE_API_SECRET --data-file=-
# (필요 시) NAVER_DATALAB_*, HIRING_DATALAB_*, KIWOOM_*, 소셜 *_CLIENT_SECRET 도 동일하게
```

## 5. 이미지 빌드/푸시 (Cloud Build)
```bash
gcloud artifacts repositories create sa-images --repository-format=docker --location=$REGION
# database(경량 migrate), agent-worker, main-server 는 빌드 컨텍스트=레포 루트
gcloud builds submit --tag $AR/database:demo     --config=/dev/stdin <<'YAML'
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','-f','database/Dockerfile','-t','$_IMG','.']
images: ['$_IMG']
substitutions: {_IMG: 'REPLACED'}
YAML
# ↑ 간단히는 로컬 docker 로:  docker build -f database/Dockerfile -t $AR/database:demo . && docker push ...
gcloud builds submit --tag $AR/agent-worker:demo --dockerfile services/agent-worker/Dockerfile .
gcloud builds submit --tag $AR/main-server:demo  --dockerfile services/main-server/Dockerfile .
```
> `gcloud builds submit --dockerfile` 가 안 되는 버전이면 `cloudbuild.yaml` 로 `-f` 지정하거나 로컬 `docker build`+`docker push` 사용. **web(FE)은 §8에서 BE URL 확정 후 빌드**(NEXT_PUBLIC 인라인).

## 6. 마이그레이션 (Cloud Run Job — 인스턴스별 2회, owner)
> 한 Job 을 두 번 재구성해 **타깃별로** 적용한다. `migrate.py` DSN 해석:
> `--target collection` → `DATABASE_URL`, `--target backend` → `BACKEND_MIGRATE_DATABASE_URL`.
> 그래서 Job 의 secret 매핑을 타깃에 맞춰 바꾼다.
```bash
# (1) 수집 DB(sa-pg) ← collection + all 마이그/시드
gcloud run jobs create sa-migrate --image=$AR/database:demo --region=$REGION --vpc-connector=sa-conn \
  --set-secrets=DATABASE_URL=MIGRATE_DATABASE_URL:latest \
  --args=apply,--target,collection,--seeds
gcloud run jobs execute sa-migrate --region=$REGION --wait

# (2) 백엔드 DB(sa-be) ← backend + all 마이그/시드
gcloud run jobs update sa-migrate --region=$REGION \
  --set-secrets=BACKEND_MIGRATE_DATABASE_URL=BACKEND_MIGRATE_DATABASE_URL:latest \
  --args=apply,--target,backend,--seeds
gcloud run jobs execute sa-migrate --region=$REGION --wait

# 검증: 타깃별 status (각자 기대 목록만 표시)
gcloud run jobs update sa-migrate --region=$REGION \
  --set-secrets=DATABASE_URL=MIGRATE_DATABASE_URL:latest --args=status,--target,collection
gcloud run jobs execute sa-migrate --region=$REGION --wait
gcloud run jobs update sa-migrate --region=$REGION \
  --set-secrets=BACKEND_MIGRATE_DATABASE_URL=BACKEND_MIGRATE_DATABASE_URL:latest --args=status,--target,backend
gcloud run jobs execute sa-migrate --region=$REGION --wait
```
> - 수집 DB: collection 6 (0001·0002·0003·0005·0005b·0006) 적용 — `api.*` view + `signal_worker`/`signal_backend` 롤.
> - 백엔드 DB: backend 5 (0001·0002·0004·0005·0007) 적용 — BACKEND15 + PUBLISHED6 + 발행용 `api.*`.
> - 권한 오류(CREATEROLE 없음) 시: 각 Cloud SQL 콘솔에서 롤을 먼저 만들고 재실행(마이그는 `IF NOT EXISTS`).
> - cross-DB FK 는 백엔드 baseline 이 이미 제거 — 두 인스턴스는 서로의 테이블을 참조하지 않는다.

## 7. 제한 롤 컷오버 (선택 — 보안 강화; 데모는 건너뛰고 owner 로 띄워도 동작)
> 2-인스턴스에선 각 롤이 자기 인스턴스에 산다: `signal_worker`→**sa-pg**, `signal_backend`→**sa-be**.
> 코드 변경 없이 **secret 값(DSN)만** 제한 롤로 교체한다.
```bash
# 수집 sa-pg 에 붙어(콘솔/Cloud SQL Studio):  ALTER ROLE signal_worker  PASSWORD '<WPASS>';
# 백엔드 sa-be 에 붙어:                        ALTER ROLE signal_backend PASSWORD '<BPASS>';
printf '%s' "postgresql://signal_worker:<WPASS>@$PRIVATE_IP:5432/signal_alpha?sslmode=require"     | gcloud secrets versions add WORKER_DATABASE_URL  --data-file=-
printf '%s' "postgresql://signal_backend:<BPASS>@$BE_PRIVATE_IP:5432/signal_alpha?sslmode=require" | gcloud secrets versions add BACKEND_DATABASE_URL --data-file=-
# 이후 BE/Worker 재배포(또는 새 리비전)면 끝 — 이미지 불변.
```

## 8. BE(main-server) → Cloud Run  ➜  9. FE 빌드  ➜  10. CORS 갱신 (2-pass)
```bash
# 8) BE 배포 — main-server 의 DATABASE_URL = 백엔드 인스턴스(sa-be). 코드 변경 없이 secret 만 백엔드 DSN.
#    BE 는 수집 DB(sa-pg)에 직접 붙지 않는다(발행 사본을 백엔드에서 읽음). CORS 는 임시; FE URL 확정 후 §10에서 갱신.
gcloud run deploy sa-backend --image=$AR/main-server:demo --region=$REGION \
  --allow-unauthenticated --vpc-connector=sa-conn --vpc-egress=private-ranges-only \
  --set-secrets=DATABASE_URL=BACKEND_DATABASE_URL:latest,AUTH_SECRET_KEY=AUTH_SECRET_KEY:latest,PORTONE_API_SECRET=PORTONE_API_SECRET:latest \
  --set-env-vars=APP_ENV=production,COOKIE_SECURE=true,COOKIE_SAMESITE=none,CORS_ALLOW_ORIGINS=https://placeholder.invalid
export BE_URL=$(gcloud run services describe sa-backend --region=$REGION --format='value(status.url)')
echo "BE = $BE_URL"

# 9) FE 빌드 — NEXT_PUBLIC_* 는 빌드타임 인라인이라 BE_URL 을 build-arg 로 넣어 빌드
gcloud builds submit web/ --tag $AR/web:demo \
  --substitutions=_BE="$BE_URL" \
  --config=/dev/stdin <<'YAML'
steps:
- name: gcr.io/cloud-builders/docker
  args: ['build','--build-arg','NEXT_PUBLIC_MAIN_API_BASE_URL=${_BE}','-t','$AR/web:demo','web']
images: ['$AR/web:demo']
YAML
# (로컬 docker 면: docker build --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=$BE_URL -t $AR/web:demo web/ && docker push ...)
gcloud run deploy sa-frontend --image=$AR/web:demo --region=$REGION --allow-unauthenticated
export FE_URL=$(gcloud run services describe sa-frontend --region=$REGION --format='value(status.url)')
echo "FE = $FE_URL"

# 10) BE 의 CORS 를 실제 FE URL 로 갱신(쿠키 credentials 때문에 정확한 오리진 필수)
gcloud run services update sa-backend --region=$REGION \
  --update-env-vars=CORS_ALLOW_ORIGINS=$FE_URL
```
> ⚠️ APP_ENV=production 이면 BE 부팅 시 G5/G6 가드가 동작 — AUTH_SECRET_KEY 가 dev 기본값이거나 CORS 가 `*`/빈값이면 **부팅 실패**(의도된 안전장치). 위 순서대로면 통과한다.

## 11. Worker(agent-worker) → GCE VM (상시·내부·단일)
```bash
gcloud compute instances create-with-container sa-worker --zone=$ZONE --machine-type=e2-small \
  --network=sa-vpc --no-address --tags=sa-worker \
  --service-account=sa-worker@$PROJECT.iam.gserviceaccount.com \
  --scopes=cloud-platform \
  --container-image=$AR/agent-worker:demo \
  --container-env=REPORT_STORAGE_BACKEND=gcs,GCS_REPORT_BUCKET=signal-alpha-reports,GCP_PROJECT_ID=$PROJECT,PRICE_COLLECTOR_ENABLED=true,QUEUE_DRAIN_DAEMON_ENABLED=true,KIWOOM_API_BASE=https://mockapi.kiwoom.com
# 워커는 DB DSN 2개 주입: DATABASE_URL(=WORKER, sa-pg) + BACKEND_DATABASE_URL(=sa-be, 발행 대상).
# create-with-container 는 Secret 직접 못 읽으므로 VM SSH 후 docker run 으로 둘 다 주입(가장 간단):
#   docker run ... \
#     -e DATABASE_URL="$(gcloud secrets versions access latest --secret=WORKER_DATABASE_URL)" \
#     -e BACKEND_DATABASE_URL="$(gcloud secrets versions access latest --secret=BACKEND_DATABASE_URL)" ...
# ⚠️ BACKEND_DATABASE_URL 미주입이면 워커는 단일 DB 모드로 동작 → 발행(publish_stock) no-op → 백엔드 api.* 가 0행.
# 인터넷 egress(외부 API 수집)가 필요하면 Cloud NAT 구성(내부 전용 VM이라).
```
> Worker 는 **단일 인스턴스**만(가격/ops/드레인 데몬이 advisory lock 으로 중복 기동 방지). 외부 포트(:8011) 방화벽 규칙은 만들지 않는다(내부 전용).
> **(#11) 큐 소비 필수**: `QUEUE_DRAIN_DAEMON_ENABLED=true` 가 있어야 워커가 `processing_queue` 를 발행
> (`PUBLISH_SIGNALS`)까지 연속 소비한다. 미설정이면 큐가 쌓이기만 하고 리포트가 발행되지 않는다.
> 데모는 단일 통합 워커(드레인+가격 데몬 동시)면 충분하고, 규모가 커지면 수집기
> (`run_collector_instance.py`)·스케줄러(`run_scheduler_instance.py`)를 별도 인스턴스로 분리한다.

## 12. 화면 확인 ✅
```bash
echo "브라우저로 접속:  $FE_URL"
curl -s -o /dev/null -w "BE /health = %{http_code}\n" $BE_URL/health   # 200 (DB ok)
```
- 브라우저에서 `$FE_URL` 열기 → 홈/대시보드 렌더 확인.
- 회원가입/로그인 → 쿠키(`sa_refresh`)가 잡히고 로그인 유지되는지(교차도메인이면 §13 점검).
- 데이터가 비어 있으면 alt-data 파이프라인을 1회 실행(아래) 후 새로고침.
```bash
# final_signals 채우기: GitHub Actions > "AltData Pipeline" > Run workflow (Secrets 선등록 필요)
# 또는 로컬에서 DATABASE_URL 지정 후:
#   cd services/agent-worker && uv run python run_collectors.py --datalab-only \
#     && uv run python run_normalizers.py && uv run python run_analyzers.py
```
> **발행 왕복 스모크(2-인스턴스 핵심):** AGGREGATE 가 final_signal 을 만들면 워커가 `PUBLISH_SIGNALS`
> 를 인큐 → `publish_stock` 이 PUBLISHED 6테이블을 sa-pg→sa-be 로 멱등 복사한다. 백엔드가 채워졌는지:
> 백엔드 DSN 으로 `SELECT count(*) FROM api.signals_current;` 가 1 이상이면 발행 도착 OK.
> (수집 DB 엔 산출물이 있는데 백엔드가 0행이면 → 워커에 `BACKEND_DATABASE_URL` 미주입을 의심, §13.)

## 13. 트러블슈팅
- **로그인이 안 풀림/쿠키 안 잡힘**: BE `CORS_ALLOW_ORIGINS` 가 정확히 `$FE_URL` 인지, `COOKIE_SECURE=true`·`COOKIE_SAMESITE=none` 인지(Cloud Run 은 HTTPS라 충족). FE↔BE 가 다른 도메인이면 브라우저 fetch 가 `credentials:'include'` 인지.
- **BE 부팅 실패(Cloud Run 리비전 unhealthy)**: 로그에 "Invalid production configuration" → AUTH_SECRET_KEY/CORS/쿠키 env 점검(G5/G6 가드).
- **/health 503**: DB 연결 실패 — VPC 커넥터·Private IP(둘 중 어느 인스턴스인지)·시크릿 DSN·sslmode 확인.
  BE 는 sa-be, 워커는 sa-pg 가 정답.
- **화면은 뜨는데 데이터 없음**: 파이프라인(§12) 미실행, 또는 `final_signals.is_published` 미발행, 또는
  **워커에 `BACKEND_DATABASE_URL` 미주입**(발행 no-op → 백엔드 `api.*` 0행). §11 의 docker run env 2개 확인.
- **마이그가 한쪽 인스턴스에만 적용됨**: §6 의 2회 실행 중 하나 누락 — 타깃별 secret 매핑(`DATABASE_URL` vs
  `BACKEND_MIGRATE_DATABASE_URL`)과 `--target` 인자가 짝이 맞는지 확인. `status --target` 으로 각자 점검.
- **백엔드에 수집 테이블이 보이거나 그 반대**: 잘못된 인스턴스에 잘못된 타깃 적용 — 해당 인스턴스를
  그린필드(`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`) 후 올바른 타깃으로 §6 재적용.

## 비용/정리
- 데모 후: `gcloud run services delete sa-frontend sa-backend`, `gcloud compute instances delete sa-worker`,
  **`gcloud sql instances delete sa-pg sa-be`**(인스턴스 2대), 커넥터/버킷 삭제로 과금 중단.
  크레딧 잔액은 콘솔 Billing 에서 확인.
