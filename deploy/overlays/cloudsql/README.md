# Cloud SQL ×2 오버레이 (관리형 DB + GKE 앱 + Argo CD)

base(`deploy/k8s`)는 DB를 **클러스터 내 Postgres(StatefulSet ×2)**로 띄운다.
이 오버레이는 같은 앱을 **클러스터 밖 관리형 Cloud SQL 2대**(수집 `sa-pg` / 백엔드 `sa-be`)에
붙이는 변형이다. 앱(web · main-server · agent-worker · collector · scheduler · altdata-cronjob)은
그대로 GKE에 살고, Argo CD가 GitOps로 자동 배포한다.

## base 대비 차이 (kustomize 로 자동 적용)
1. `postgres-collection` · `postgres-backend` (Service + StatefulSet) **제거** → 관리형 Cloud SQL 사용.
2. 마이그 Job `db-migrate-{collection,backend}` 의 `wait-postgres` initContainer **제거**
   (base 는 in-cluster Service 명으로 `pg_isready` 하는데, Cloud SQL 전환 시 그 Service 가 없어
   영원히 대기하게 됨. Cloud SQL 은 상시 가동이라 대기 불필요).
3. 이미지 핀을 오버레이에서 덮어씀(base 의 `PROJECT_ID` placeholder 를 직접 수정하지 않음).

> **앱 매니페스트는 일절 수정하지 않는다.** DB 접속은 전부 Secret `signal-alpha-secrets` 의
> `*_DATABASE_URL` 로만 주입되므로, host 만 Cloud SQL 로 두면 base 앱이 그대로 Cloud SQL 에 붙는다.

## 사전 준비 (사용자, gcloud)
1. **Cloud SQL ×2 생성** — `docs/gcp-deploy-runbook.md` §1 그대로
   (`sa-pg` 수집 / `sa-be` 백엔드, Private IP, DB `signal_alpha`).
2. **GKE 클러스터를 같은 VPC(`sa-vpc`)에 VPC-native 로 생성** → Pod 가 Cloud SQL **Private IP** 로
   직접 접속(사이드카 불필요). 예:
   ```
   gcloud container clusters create sa-gke --zone=asia-northeast3-a \
     --network=sa-vpc --subnetwork=<subnet> --enable-ip-alias \
     --num-nodes=1 --machine-type=e2-medium
   ```
   (대안: Cloud SQL Auth Proxy 사이드카 + Workload Identity. Private IP 가 가장 단순.)
3. **이미지 빌드/푸시** — `deploy/README.md` §1 → `kustomization.yaml` 의 `PROJECT_ID`/`newTag` 핀.

## Secret 생성 (out-of-band — 평문 커밋 금지)
DSN host 를 **Cloud SQL Private IP** 로 둔다(키 이름은 base 와 동일):
```bash
kubectl create namespace signal-alpha
kubectl -n signal-alpha create secret generic signal-alpha-secrets \
  --from-literal=WORKER_DATABASE_URL='postgresql://signal_alpha:PW@<SA_PG_IP>:5432/signal_alpha?sslmode=require' \
  --from-literal=BACKEND_DATABASE_URL='postgresql://signal_alpha:PW@<SA_BE_IP>:5432/signal_alpha?sslmode=require' \
  --from-literal=MIGRATE_DATABASE_URL='postgresql://signal_alpha:PW@<SA_PG_IP>:5432/signal_alpha?sslmode=require' \
  --from-literal=BACKEND_MIGRATE_DATABASE_URL='postgresql://signal_alpha:PW@<SA_BE_IP>:5432/signal_alpha?sslmode=require' \
  --from-literal=GEMINI_API_KEY='...' --from-literal=DART_API_KEY='...' \
  --from-literal=KIWOOM_APP_KEY='...' --from-literal=KIWOOM_APP_SECRET='...' \
  --from-literal=INTERNAL_API_TOKEN='...'
```
- 워커는 `DATABASE_URL`(=WORKER, sa-pg) + `BACKEND_DATABASE_URL`(=sa-be, 발행 대상) 둘 다 필요.
- POSTGRES_PASSWORD 는 in-cluster Postgres 전용이라 Cloud SQL 변형에선 불필요(있어도 무해).
- 전체 키 목록 = `deploy/k8s/secret.example.yaml`.

## 배포 (Argo CD)
base 의 `deploy/argocd/application.yaml`(in-cluster) **대신 이 오버레이의 Application** 적용 — 택1:
```bash
kubectl apply -f deploy/argocd/project.yaml \
              -f deploy/overlays/cloudsql/application.yaml
```
Argo CD 동기화 순서(wave): 마이그 Job ×2(wave 1, Sync 훅) → 앱(wave 2) → Ingress(wave 3).
DB(wave 0 Postgres)는 제거됐고 Cloud SQL 이 그 자리를 대신한다.
> 병합 전이면 `application.yaml` 의 `targetRevision` 을 `feat/deploy-cloudsql-overlay` 로,
> main 병합 후엔 `main` 으로 둔다.

## 로컬 렌더 검증 (클러스터 불요)
```bash
kubectl kustomize deploy/overlays/cloudsql            # 렌더 확인(Postgres/StatefulSet 없어야 함)
kubectl kustomize deploy/overlays/cloudsql | kubectl apply --dry-run=server -f -   # 클러스터 있을 때 스키마 검증
```

## 발행 왕복 스모크 (배포 성공 판정)
alt-data 1회 실행 → 워커가 `PUBLISH_SIGNALS` 인큐 → `publish_stock` 이 sa-pg→sa-be PUBLISHED 6테이블 복사.
**백엔드 DSN 으로 `SELECT count(*) FROM api.signals_current;` ≥ 1 이면 발행 도착 OK.**
(0행이면 워커에 `BACKEND_DATABASE_URL` 미주입 의심.)
