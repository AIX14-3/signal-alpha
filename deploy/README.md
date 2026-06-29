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
| 수집 DB / 백엔드 DB | `k8s/postgres-collection.yaml` · `postgres-backend.yaml` | StatefulSet ×2 |
| 마이그레이션 | `k8s/db-migrate-{collection,backend}-job.yaml` | `--target` 각각, Argo Sync 훅 |

이미지(`agent-worker`)는 worker/collector/scheduler/alt-data 가 **공유**하고 command 만 다르다.

## 1) 이미지 빌드 & 푸시 (레포 루트에서, context=`.`)
```bash
REG=asia-northeast3-docker.pkg.dev/PROJECT_ID/signal-alpha
docker build -f services/agent-worker/Dockerfile -t $REG/agent-worker:TAG .
docker build -f services/main-server/Dockerfile  -t $REG/main-server:TAG  .
docker build -f web/Dockerfile --target runner \
  --build-arg NEXT_PUBLIC_MAIN_API_BASE_URL=https://<INGRESS_HOST> -t $REG/web:TAG ./web
docker build -f database/Dockerfile -t $REG/db-migrate:TAG .
docker push $REG/agent-worker:TAG && docker push $REG/main-server:TAG \
  && docker push $REG/web:TAG && docker push $REG/db-migrate:TAG
# k8s/kustomization.yaml 의 images: PROJECT_ID/newTag 를 위 값으로 핀
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

## 로컬 렌더 검증(클러스터 불요)
```bash
kubectl kustomize deploy/k8s | kubectl apply --dry-run=client -f -
```
