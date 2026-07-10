# 배포 — GKE + Argo CD (GitOps)

signal-alpha 5 컴퓨트 유닛(web · main-server · agent-worker · collector · scheduler) +
2 DB 인스턴스(수집/백엔드)를 쿠버네티스로 배포한다. 개념·비용·절차 설명은
[docs/k8s-argocd-deployment.md](../docs/k8s-argocd-deployment.md) 참고.

```
deploy/
  k8s/        # 매니페스트(kustomize) — Argo CD 가 이 디렉터리를 sync
  argocd/     # Argo CD AppProject + Application
```

## 유닛 ↔ 매니페스트
| 유닛 | 매니페스트 | 비고 |
|---|---|---|
| web | `k8s/web.yaml` | Next.js(3000), Dockerfile runner 타깃 |
| main-server | `k8s/main-server.yaml` | FastAPI(8000), 백엔드 DB |
| agent-worker | `k8s/agent-worker.yaml` | 8011 + 큐 드레인 데몬, `PRICE_COLLECTOR_ENABLED=false` |
| **collector** | `k8s/collector.yaml` | Kiwoom 실시간 데몬(`run_collector_instance.py`), Service 없음 |
| **scheduler** | `k8s/scheduler.yaml` | `collection_schedules` 폴링(`run_scheduler_instance.py`) |
| alt-data 배치 | `k8s/altdata-cronjob.yaml` | patent/datalab, 매일 04:30 KST |
| **hiring 크롤 배치** | `k8s/hiring-cronjob.yaml` | Selenium+Chrome 크롤→분석, 매일 04:30 KST. **전용 이미지**(`hiring-crawler`) |
| 수집 DB / 백엔드 DB | `k8s/postgres-collection.yaml` · `postgres-backend.yaml` | StatefulSet ×2 |
| 마이그레이션 | `k8s/db-migrate-{collection,backend}-job.yaml` | `--target` 각각, Argo Sync 훅 |

이미지(`agent-worker`)는 worker/collector/scheduler/alt-data 가 **공유**하고 command 만 다르다.
**예외: hiring 크롤러**는 chromium 의존(수백 MB)을 격리하려 `agent-worker` 위에 chromium 을 얹은
**별도 이미지(`hiring-crawler`, `Dockerfile.crawler`)**를 쓴다 — 나머지 유닛은 슬림 base 유지.

## 0) 자동 배포 (main 머지 → 배포)

`.github/workflows/deploy.yml` 이 `main` push 시 이미지 5개를 빌드·푸시하고 `k8s/kustomization.yaml`
의 태그를 커밋 SHA(`sha-<12자>`)로 핀해 되돌려 커밋한다. **그 매니페스트 변경**을 Argo CD 가 자동
sync 한다(폴링 ~3분). 아래 §1·§2 수동 절차는 이제 폴백/최초 부트스트랩용이다.

> **왜 태그를 바꿔야 하나.** 매니페스트가 `:latest` 로 고정돼 있으면 코드를 바꿔도 `deploy/k8s` 에
> diff 가 없다 → Argo 는 "바뀐 게 없다"고 보고 아무것도 하지 않고, 파드는 옛 이미지를 계속 돌린다.
> 커밋 SHA 태그는 불변이라 **롤백·추적**도 된다.

### GCP 인증 — 키 없이(Workload Identity Federation)

이 조직은 조직 정책 `constraints/iam.disableServiceAccountKeyCreation` 으로 **서비스 계정 JSON 키
발급을 금지**한다(옳은 정책 — 만료 없는 장기 키는 유출되면 그대로 뚫린다). 그래서 GitHub 이 발급한
단명 OIDC 토큰을 GCP 가 직접 신뢰하게 한다. **저장되는 비밀이 없다.**

구성(1회, 이미 적용됨):

```bash
PROJECT=signal-alpha-demo; PROJECT_NUMBER=133341272598; REPO=AIX14-3/signal-alpha
SA=gha-deployer@$PROJECT.iam.gserviceaccount.com

gcloud iam service-accounts create gha-deployer --project=$PROJECT
# 프로젝트 전체가 아니라 **그 저장소 하나에만** 쓰기 권한(최소 권한).
gcloud artifacts repositories add-iam-policy-binding signal-alpha \
  --location=asia-northeast3 --project=$PROJECT \
  --member="serviceAccount:$SA" --role=roles/artifactregistry.writer

gcloud services enable sts.googleapis.com --project=$PROJECT
gcloud iam workload-identity-pools create github --location=global --project=$PROJECT
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global --workload-identity-pool=github --project=$PROJECT \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='$REPO'"   # ← 이게 없으면 아무 레포나 이 SA 를 빌려 쓴다
gcloud iam service-accounts add-iam-policy-binding $SA --project=$PROJECT \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```

### GitHub Variables (Settings → Secrets and variables → Actions → Variables)

Secret 은 하나도 필요 없다. 아래는 전부 공개 값이다.

| 이름 | 값 |
|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/133341272598/locations/global/workloadIdentityPools/github/providers/github-oidc` |
| `GCP_SERVICE_ACCOUNT` | `gha-deployer@signal-alpha-demo.iam.gserviceaccount.com` |
| `NEXT_PUBLIC_MAIN_API_BASE_URL` | `https://api.signal-alpha.cloud` |
| `NEXT_PUBLIC_*` (나머지 6개) | `web/Dockerfile` 의 `ARG NEXT_PUBLIC_*` 와 1:1 |

`NEXT_PUBLIC_*` 은 **빌드 타임에 번들로 인라인**된다. 런타임 env 로는 안 바뀌므로 도메인이 바뀌면
반드시 Variable 을 고치고 재빌드해야 한다. 하나라도 빠지면 빈 문자열이 되어 그 기능만 배포본에서
조용히 죽는다 — `web/tests/deploy-build-args.test.mjs` 가 Dockerfile 의 ARG 목록과 대조해 막는다.

**루프 방지**: 워크플로가 `main` 에 미는 핀 커밋은 `kustomization.yaml` 한 파일만 건드리는데,
`on.push.paths` 가 그 파일을 제외하므로 자신을 다시 트리거하지 않는다(게다가 `GITHUB_TOKEN` 으로
민 커밋은 애초에 워크플로를 트리거하지 않는다 — 이중 방어).

**롤백**: Actions 에서 `Deploy` 를 `workflow_dispatch` 로 돌리며 `tag` 에 옛 태그를 넣는다.

⚠️`main` 에 브랜치 보호(직접 push 금지)가 걸려 있으면 마지막 핀 커밋 push 가 실패한다. 그 경우
`github-actions[bot]` 을 우회 허용 목록에 넣거나, 핀 커밋을 PR 로 여는 방식으로 바꿔야 한다.

## 1) 이미지 빌드 & 푸시 (수동 · 레포 루트에서, context=`.`)
```bash
REG=asia-northeast3-docker.pkg.dev/PROJECT_ID/signal-alpha
docker build -f services/agent-worker/Dockerfile -t $REG/agent-worker:TAG .
docker build -f services/main-server/Dockerfile  -t $REG/main-server:TAG  .
# web 은 BE 주소(NEXT_PUBLIC_*)를 빌드타임에 인라인 → 커스텀 도메인이면 api 서브도메인으로.
#   서브도메인 분리 구조(docs/gabia-domain-cors-setup.md): FE=www.<도메인>, BE=api.<도메인>.
#   ⚠️ 도메인 바뀌면 런타임 env 로는 안 바뀜 → 반드시 이 build-arg 로 재빌드.
docker build -f web/Dockerfile --target runner \
  --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=https://api.<도메인> -t $REG/web:TAG ./web
docker build -f database/Dockerfile -t $REG/db-migrate:TAG .
# hiring 크롤러 전용 이미지 — agent-worker 빌드 후 그 위에 chromium 을 얹는다(FROM base).
docker build -f services/agent-worker/Dockerfile.crawler -t $REG/hiring-crawler:TAG \
  --build-arg BASE_IMAGE=$REG/agent-worker:TAG .
docker push $REG/agent-worker:TAG && docker push $REG/main-server:TAG \
  && docker push $REG/web:TAG && docker push $REG/db-migrate:TAG \
  && docker push $REG/hiring-crawler:TAG
# k8s/kustomization.yaml 의 images: PROJECT_ID/newTag 를 위 값으로 핀
# ⚠️ hiring-crawler 를 빌드/푸시하지 않으면 hiring-crawl CronJob 이 ImagePullBackOff 로 죽는다.
```

## 2) 비밀 생성 (out-of-band — 평문 커밋 금지)
`k8s/secret.example.yaml` 의 키를 채워 생성:
```bash
kubectl create namespace signal-alpha
kubectl -n signal-alpha create secret generic signal-alpha-secrets \
  --from-literal=WORKER_DATABASE_URL=... --from-literal=BACKEND_DATABASE_URL=... \
  --from-literal=MIGRATE_DATABASE_URL=... --from-literal=BACKEND_MIGRATE_DATABASE_URL=... \
  --from-literal=POSTGRES_PASSWORD=... --from-literal=KIWOOM_APP_KEY=... ...
```

## 3) Argo CD 등록
```bash
kubectl apply -f deploy/argocd/project.yaml -f deploy/argocd/application.yaml
```
Argo CD 가 `deploy/k8s/` 를 동기화: Postgres ×2(wave 0) → 마이그 Job ×2(wave 1, Sync 훅) →
앱(wave 2) → Ingress(wave 3). 이후 매니페스트를 Git 에 push 하면 자동 sync.

## 리포트 저장 백엔드 (local → gcs)
초기 인클러스터 브링업은 `configmap.yaml` `REPORT_STORAGE_BACKEND: local` + agent-worker 의
RWO PVC(`agent-worker-reports`, `/data/report-storage`)를 쓴다. GKE 에는 Cloud Run/GCE 같은
attached service account 가 없어 `storage.Client()` ADC 가 인증되지 않기 때문이다.
리포트 외부 서빙이 필요한 컷오버 시점에 `gcs` 로 전환:
1. configmap `REPORT_STORAGE_BACKEND: gcs`,
2. GSA 에 버킷 `roles/storage.objectAdmin` 부여,
3. agent-worker KSA ↔ GSA **Workload Identity** 바인딩(ADC 동작 재현). 코드 변경 없음.

## 워커류 단일기동 보강
`agent-worker`·`collector` 는 advisory lock 단일 소비 데몬이라 `strategy: Recreate` +
`terminationGracePeriodSeconds: 60` 을 둔다(기본 RollingUpdate 면 롤아웃 중 데몬이 순간 2개).

## 로컬 렌더 검증(클러스터 불요)
```bash
kubectl kustomize deploy/k8s | kubectl apply --dry-run=client -f -
```
