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

## 1) 이미지 빌드 & 푸시 (레포 루트에서, context=`.`)
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
