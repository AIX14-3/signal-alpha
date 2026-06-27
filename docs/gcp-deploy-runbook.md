# GCP 배포 런북 (데모 — 직접 실행용)

> 목표: FE/BE=**Cloud Run**, Worker=**GCE VM**, DB=**Cloud SQL(Model A 롤격리)**, 리포트=**GCS**.
> 신규 GCP 계정 **$300 크레딧(90일)** 으로 사실상 무료. 끝까지 따라 하면 브라우저에서 화면이 뜬다.
> 모든 `<...>` 는 본인 값으로 치환. 명령은 Cloud Shell 또는 `gcloud` 설치된 로컬에서 실행.

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

## 1. 네트워크 + Cloud SQL (Private IP)
```bash
gcloud compute networks create sa-vpc --subnet-mode=auto
gcloud compute addresses create google-managed-services-sa-vpc \
  --global --purpose=VPC_PEERING --prefix-length=16 --network=sa-vpc
gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-sa-vpc --network=sa-vpc

gcloud sql instances create sa-pg --database-version=POSTGRES_16 --tier=db-g1-small \
  --region=$REGION --storage-size=10GB --storage-auto-increase \
  --network=projects/$PROJECT/global/networks/sa-vpc --no-assign-ip --backup
gcloud sql users set-password postgres --instance=sa-pg --password='<OWNER_PASS>'
gcloud sql databases create signal_alpha --instance=sa-pg
# Private IP 확인(이후 DSN 에 사용)
export PRIVATE_IP=$(gcloud sql instances describe sa-pg --format='value(ipAddresses[0].ipAddress)')
echo "Cloud SQL private IP = $PRIVATE_IP"
```
> pgvector 는 제거됐으므로 `CREATE EXTENSION vector` 는 **하지 않는다**.

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
> 컷오버 전: 3개 DB URL 모두 **owner** 로 둔다(폴백). §7에서 worker/backend 만 제한 롤로 교체.
```bash
DSN_OWNER="postgresql://postgres:<OWNER_PASS>@$PRIVATE_IP:5432/signal_alpha?sslmode=require"
printf '%s' "$DSN_OWNER" | gcloud secrets create MIGRATE_DATABASE_URL --data-file=-
printf '%s' "$DSN_OWNER" | gcloud secrets create WORKER_DATABASE_URL  --data-file=-
printf '%s' "$DSN_OWNER" | gcloud secrets create BACKEND_DATABASE_URL --data-file=-
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

## 6. 마이그레이션 (Cloud Run Job, owner)
```bash
gcloud run jobs create sa-migrate --image=$AR/database:demo --region=$REGION --vpc-connector=sa-conn \
  --set-secrets=DATABASE_URL=MIGRATE_DATABASE_URL:latest --args=apply,--seeds
gcloud run jobs execute sa-migrate --region=$REGION --wait
# 검증: 상태 확인용으로 args 만 바꿔 1회 더 실행 가능
gcloud run jobs update sa-migrate --region=$REGION --args=status && gcloud run jobs execute sa-migrate --region=$REGION --wait
```
> 이때 `api.*` 읽기 view + `signal_worker`/`signal_backend` 롤(비번 없음)이 생성된다.
> 권한 오류(CREATEROLE 없음) 시: Cloud SQL 콘솔에서 두 롤을 먼저 만들고 재실행(마이그는 `IF NOT EXISTS`).

## 7. Model A 컷오버 (선택 — 보안 강화; 데모는 건너뛰고 owner 로 띄워도 동작)
```bash
# Cloud SQL 에 붙어(콘솔/Cloud SQL Studio) out-of-band 로 비밀번호 부여:
#   ALTER ROLE signal_worker  PASSWORD '<WPASS>';
#   ALTER ROLE signal_backend PASSWORD '<BPASS>';
# 그리고 secret 을 제한 롤로 교체:
printf '%s' "postgresql://signal_worker:<WPASS>@$PRIVATE_IP:5432/signal_alpha?sslmode=require"  | gcloud secrets versions add WORKER_DATABASE_URL  --data-file=-
printf '%s' "postgresql://signal_backend:<BPASS>@$PRIVATE_IP:5432/signal_alpha?sslmode=require" | gcloud secrets versions add BACKEND_DATABASE_URL --data-file=-
# 이후 BE/Worker 재배포(또는 새 리비전)면 끝 — 이미지 불변.
```

## 8. BE(main-server) → Cloud Run  ➜  9. FE 빌드  ➜  10. CORS 갱신 (2-pass)
```bash
# 8) BE 배포 (CORS 는 일단 임시; FE URL 확정 후 §10에서 갱신)
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
> (#11 업데이트) 운영 토폴로지는 worker/collector/scheduler **3 유닛**으로 분리 가능하지만, 이 데모는
> `agent-worker`를 **단일 통합 기동**으로 띄운다 — 드레인 데몬(`QUEUE_DRAIN_DAEMON_ENABLED`)이 큐를 끝단
> 발행까지 소비하고 가격 수집(`PRICE_COLLECTOR_ENABLED`)도 같은 VM에 내장. 유닛 분리/2-DB 토폴로지는
> [architecture-diagram.md](./architecture-diagram.md) 참조.
```bash
gcloud compute instances create-with-container sa-worker --zone=$ZONE --machine-type=e2-small \
  --network=sa-vpc --no-address --tags=sa-worker \
  --service-account=sa-worker@$PROJECT.iam.gserviceaccount.com \
  --scopes=cloud-platform \
  --container-image=$AR/agent-worker:demo \
  --container-env=REPORT_STORAGE_BACKEND=gcs,GCS_REPORT_BUCKET=signal-alpha-reports,GCP_PROJECT_ID=$PROJECT,QUEUE_DRAIN_DAEMON_ENABLED=true,PRICE_COLLECTOR_ENABLED=true,KIWOOM_API_BASE=https://mockapi.kiwoom.com
# DATABASE_URL(=WORKER) 주입: create-with-container 는 Secret 직접 못 읽으므로 메타데이터/startup 으로 주입하거나
# 가장 간단히 VM SSH 후 docker run -e DATABASE_URL="$(gcloud secrets versions access latest --secret=WORKER_DATABASE_URL)" ...
# 인터넷 egress(외부 API 수집)가 필요하면 Cloud NAT 구성(내부 전용 VM이라).
```
> Worker 는 **단일 인스턴스**만(가격/ops 데몬 advisory lock). 외부 포트(:8011) 방화벽 규칙은 만들지 않는다(내부 전용).

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

## 13. 트러블슈팅
- **로그인이 안 풀림/쿠키 안 잡힘**: BE `CORS_ALLOW_ORIGINS` 가 정확히 `$FE_URL` 인지, `COOKIE_SECURE=true`·`COOKIE_SAMESITE=none` 인지(Cloud Run 은 HTTPS라 충족). FE↔BE 가 다른 도메인이면 브라우저 fetch 가 `credentials:'include'` 인지.
- **BE 부팅 실패(Cloud Run 리비전 unhealthy)**: 로그에 "Invalid production configuration" → AUTH_SECRET_KEY/CORS/쿠키 env 점검(G5/G6 가드).
- **/health 503**: DB 연결 실패 — VPC 커넥터·Private IP·시크릿 DSN·sslmode 확인.
- **화면은 뜨는데 데이터 없음**: 파이프라인(§12) 미실행 또는 `final_signals.is_published` 미발행.
- **Worker 가 외부 API 못 부름**: 내부 전용 VM → Cloud NAT 필요.

## 비용/정리
- 데모 후: `gcloud run services delete sa-frontend sa-backend`, `gcloud compute instances delete sa-worker`,
  `gcloud sql instances delete sa-pg`, 커넥터/버킷 삭제로 과금 중단. 크레딧 잔액은 콘솔 Billing 에서 확인.
