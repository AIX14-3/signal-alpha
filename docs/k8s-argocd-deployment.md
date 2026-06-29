# 쿠버네티스(GKE) + Argo CD 적용 가이드 (학습용)

> 이 문서는 signal-alpha를 **학습 목적으로** 쿠버네티스(GKE)에 올리고 **Argo CD**로
> GitOps 배포를 체험하기 위한 개념 설명 + 적용 가이드다. 실무 운영 표준이 아니라
> "직접 해보며 이해하기" 위한 자료다.

---

## 0. 결론 먼저: 적용 가능한가?

**가능하다. 오히려 조건이 좋다.**

- 서비스 4종(main-server / agent-worker / web / db-migrate) 모두 **Dockerfile이 이미 존재**한다.
- [`docker-compose.yml`](../docker-compose.yml)이 사실상 **k8s 매니페스트의 청사진**이다
  (환경변수·포트·의존관계가 전부 정리돼 있음).
- **외부 메시지 브로커(Redis/RabbitMQ) 없이 DB 내장 큐**(`processing_queue` 테이블)라
  구조가 단순해 쿠버네티스로 옮기기 쉽다.
- 프로젝트가 이미 GCP에 묶여 있어(Cloud SQL/GCS/Gemini) **GKE가 자연스러운 선택**이다.
  → AWS를 쓸 이유가 없다. (k8s는 클라우드 중립 표준이고, GCP의 관리형 k8s가 GKE다.)

---

## 1. 쿠버네티스(Kubernetes, k8s)란?

여러 대의 서버(=노드) 위에서 **컨테이너(도커 이미지)를 자동으로 띄우고, 죽으면 되살리고,
부하에 따라 늘리고/줄이는 "컨테이너 운영체제"**다.

지금 `docker-compose.yml`로 로컬에서 하는 일(서비스 5개 + DB 같이 띄우기)을,
**여러 서버 규모에서 자동복구·확장 기능과 함께** 하는 것이라고 보면 된다.

### 핵심 오브젝트 (docker-compose 개념과 1:1 대응)

| k8s 개념 | 한 줄 설명 | docker-compose 대응 |
|---|---|---|
| **Pod** | 컨테이너 1개(+@)를 감싼 최소 실행 단위 | `service` 컨테이너 1개 |
| **Deployment** | "이 Pod를 N개 항상 떠 있게 유지"하는 컨트롤러(죽으면 재생성) | `restart: unless-stopped` + 복제 |
| **Service** | Pod들에 고정된 내부 주소/로드밸런싱 제공 | compose의 서비스명 DNS |
| **Ingress / LoadBalancer** | 외부 트래픽을 내부 Service로 들여보냄 | `ports:` 외부 공개 |
| **ConfigMap / Secret** | 환경변수·설정 / 비밀키를 분리 보관 | `environment:` / `.env` |
| **PersistentVolume (PVC)** | 컨테이너가 죽어도 남는 디스크 | `volumes: postgres-data` |
| **Job / CronJob** | 1회성 / 주기성 배치 작업 | `db-migrate`, cron |
| **StatefulSet** | 상태를 가진 앱(DB 등)용 안정적 Pod | `postgres` |
| **Namespace** | 리소스 묶음(논리적 폴더) | (compose엔 없음) |

> 명령은 거의 다 `kubectl`로 한다: `kubectl get pods`, `kubectl logs ...`, `kubectl apply -f ...`

---

## 2. Argo CD란?

**GitOps 도구**다. "**Git 저장소에 있는 k8s 매니페스트(YAML)를 정답(desired state)으로 보고,
클러스터의 실제 상태를 거기에 자동으로 맞춰 동기화(sync)**"한다.

```
git push 로 YAML 수정  →  Argo CD가 변경 감지  →  클러스터에 자동 반영
```

- 클러스터 안에 설치되는 앱이며, **웹 UI**로 리소스 트리와 동기화 상태(Synced/Healthy)·
  틀어짐(drift)을 시각적으로 본다.
- **이미지 빌드는 하지 않는다.** 오직 "YAML대로 배포"만 담당한다.
  → 코드를 바꾸면: 이미지 재빌드/푸시 → 매니페스트의 이미지 태그 갱신 → push →
    그 변경을 Argo CD가 sync.

> ⚠️ **참고:** Argo CD는 본질적으로 "배포 관리(GitOps)" 도구다.
> 만약 보고 싶었던 것이 "수집→정규화→분석→집계 **파이프라인을 단계별 Job으로 실행**하는 것"이라면
> 그건 Argo CD가 아니라 **Argo Workflows**(DAG/배치 엔진)다. 본 문서는 **Argo CD 기준**이다.
> signal-alpha의 파이프라인 DAG는 Argo Workflows와도 잘 맞으므로, 추후 별도로 실험해 볼 수 있다.

---

## 3. signal-alpha 배포 대상 매핑

실제 매니페스트는 **[`deploy/k8s/`](../deploy/k8s/)**(kustomize) + **[`deploy/argocd/`](../deploy/argocd/)** 에 있다.
[`docker-compose.yml`](../docker-compose.yml) 의 env/포트/의존을 **5 컴퓨트 유닛 + 2 DB 인스턴스**로 번역:

| 유닛 | k8s 리소스 | 매니페스트 | 비고 |
|---|---|---|---|
| 수집 DB / 백엔드 DB | **StatefulSet ×2 + PVC + headless Service** | `postgres-collection.yaml` · `postgres-backend.yaml` | 2-인스턴스 물리 분리(`signal_worker` / `signal_backend`) |
| db-migrate | **Job ×2 (Argo Sync 훅)** | `db-migrate-{collection,backend}-job.yaml` | `apply --seeds --target collection/backend` |
| `agent-worker` | **Deployment + Service(8011)** | `agent-worker.yaml` | `QUEUE_DRAIN_DAEMON_ENABLED=true`, **`PRICE_COLLECTOR_ENABLED=false`** |
| **collector** | **Deployment**(Service 없음) | `collector.yaml` | `run_collector_instance.py` — Kiwoom 실시간 데몬(advisory lock, replicas:1) |
| **scheduler** | **Deployment** | `scheduler.yaml` | `run_scheduler_instance.py` — `BACKEND_DATABASE_URL` 로 `collection_schedules` 폴링 |
| alt-data 배치 | **CronJob**(04:30 KST) | `altdata-cronjob.yaml` | `run_collectors.py` patent/datalab → 수집 DB |
| `main-server` | **Deployment + Service(8000)** | `main-server.yaml` | 백엔드 API. DB는 백엔드 인스턴스(`api.*` view + 소유 테이블) |
| `web` | **Deployment + Service(3000)** | `web.yaml` | **Dockerfile `runner` 타깃** |
| 외부 노출 | **Ingress** | `ingress.yaml` | `/api`→main-server, `/`→web |

> `analyzer`(run_analyzers.py) 및 alt-data 후속 단계(normalizers→analyzers)는 추후 CronJob/Job 으로 확장 가능.

### 주의할 점 3가지
1. **이미지 공유**: `agent-worker` 이미지를 worker/collector/scheduler/alt-data 가 **공유**하고 **command 만** 다르다
   (`uvicorn` / `run_collector_instance.py` / `run_scheduler_instance.py` / `run_collectors.py`). 이 엔트리포인트들이
   이미지에 COPY 돼 있어야 한다(`services/agent-worker/Dockerfile`).
2. **가격 데몬 위치**: 5-유닛 분리에선 가격 데몬을 **collector 유닛**이 전담한다 → worker 는 `PRICE_COLLECTOR_ENABLED=false`.
   (단일 통합 인스턴스 학습 시에만 worker 에 `=true` 로 내장 가능.)
3. **scheduler 는 백엔드 DB 폴링**: `collection_schedules`(백엔드 DB)를 읽어 발화하므로 `BACKEND_DATABASE_URL` 필요.
   어드민(웹)이 같은 테이블을 수정해 제어한다(`main-server → worker` 직접 호출 없음).

---

## 4. 적용 절차 (단계별)

### Phase 1 — 사전 준비
- 로컬 도구: `gcloud` CLI, `kubectl`, `docker`(Docker Desktop), `argocd` CLI(선택).
- GCP: 결제 계정 연결(**신규면 $300 무료 크레딧/90일** 활성화), API 활성화
  (`container.googleapis.com`, `artifactregistry.googleapis.com`).
- **Artifact Registry** 도커 저장소 생성
  (예: `asia-northeast3-docker.pkg.dev/<PROJECT>/signal-alpha`),
  `gcloud auth configure-docker`로 인증.

### Phase 2 — 이미지 빌드 & 푸시
기존 Dockerfile 재사용(신규 작성 불필요):
- `services/agent-worker/Dockerfile` (worker/scheduler/analyzer 공용, Tesseract 포함)
- `services/main-server/Dockerfile`
- `web/Dockerfile` (**프로덕션은 기본 타깃 `runner`로 빌드**)
- `database/Dockerfile` (마이그레이션 전용 경량)

각 이미지를 빌드해 Artifact Registry에 태그/푸시.
빌드 컨텍스트는 compose와 동일하게 **레포 루트(`context: .`)** — uv workspace 때문.

### Phase 3 — 매니페스트 (`deploy/k8s/`, 작성 완료)
kustomize 로 묶인 실제 파일(§3 매핑표 참조):
`namespace.yaml`, `kustomization.yaml`, `configmap.yaml`, `secret.example.yaml`(템플릿 — 커밋 금지값),
`postgres-collection.yaml`, `postgres-backend.yaml`, `db-migrate-collection-job.yaml`,
`db-migrate-backend-job.yaml`, `agent-worker.yaml`, `collector.yaml`, `scheduler.yaml`,
`altdata-cronjob.yaml`, `main-server.yaml`, `web.yaml`, `ingress.yaml`.
적용·빌드·비밀 생성 절차는 [deploy/README.md](../deploy/README.md) 참고.

> **비밀키 목록**: `DART_API_KEY`, `GEMINI_API_KEY`/`OPENAI_API_KEY`,
> `KIWOOM_APP_KEY/SECRET`, `KIPRIS_API_KEY`, `NAVER_DATALAB_CLIENT_*`,
> DB 비밀번호, (결제) `PORTONE_*` → YAML엔 템플릿만, 실제 값은 `kubectl create secret`로 주입.

### Phase 4 — GKE 클러스터 생성 (비용 최소화)
- **존(zonal) 클러스터 1개**(예: `asia-northeast3-a`) → **관리비 면제 1개 한도 활용**.
- 노드: `e2-medium`(2vCPU/4GB) 1~2대 작은 노드풀.
- `gcloud container clusters get-credentials`로 `kubectl` 컨텍스트 연결.

### Phase 5 — 기반 리소스 수동 배포
`namespace → Secret(create) → ConfigMap → Postgres → db-migrate Job` 순으로
`kubectl apply` 후 `kubectl get pods -n signal-alpha`로 확인.
(여기까지 수동으로 해 보며 "수동 배포 vs GitOps" 차이를 체감하는 게 포인트.)

### Phase 6 — Argo CD 설치 & Application 등록
1. `kubectl create namespace argocd` → 공식 install YAML `kubectl apply`.
2. UI 접속: `kubectl port-forward svc/argocd-server -n argocd 8080:443` → `https://localhost:8080`
   (초기 비번: `argocd-initial-admin-secret`).
3. `deploy/argocd/application.yaml` 작성:
   `repoURL`=이 레포, `path`=`deploy/k8s`, `targetRevision`=브랜치,
   `destination.namespace`=`signal-alpha`, `syncPolicy.automated`(prune/selfHeal) 켜기.
4. `kubectl apply` → Argo CD가 `deploy/k8s/` 전체를 자동 배포.

### Phase 7 — 검증 (E2E)
- `kubectl get pods -n signal-alpha` → 전부 `Running`.
- `kubectl logs deploy/agent-worker -n signal-alpha` → 큐 드레인/가격 데몬 기동 로그.
- main-server `/health`, agent-worker `/health`, web 대시보드 접속.
- **GitOps 루프 시연(핵심):** `main-server.yaml`의 `replicas: 1→2` 수정 후 `git push`
  → Argo CD 자동 sync → Pod 2개로 증가 확인.
  (selfHeal 시연: `kubectl scale`로 1개로 줄이면 Argo CD가 다시 2개로 복원.)

### Phase 8 — 정리 (비용 차단)
실습 종료 시 **`gcloud container clusters delete <cluster> --zone <zone>`** (노드 과금 중단).
매니페스트는 Git에 남아 다음 실습 때 Phase 4부터 재현 가능(= GitOps의 장점).

---

## 5. 예상 비용 (GCP, 학습용)

| 항목 | 비용 |
|---|---|
| GKE 클러스터 관리비 | 존 클러스터 **1개 면제** → **$0** |
| 노드 VM (`e2-medium`) | ×1 ≈ **$25/월**, ×2 ≈ **$50/월** (상시 기준) |
| Cloud SQL | **미사용**(클러스터 내 Postgres) → **$0** |
| Argo CD | 오픈소스, 노드 자원만 사용 → **$0** |

- 신규 계정 **$300 무료 크레딧**이면 이번 실습은 사실상 전액 커버된다.
- GKE는 **시간당 과금**이므로, **하루 몇 시간만 실습하고 끝나면 클러스터를 삭제**하면
  실제 비용은 한 달 몇 달러 수준이다.
- 💡 **가장 큰 절약 포인트: 안 쓸 땐 클러스터 삭제.**

---

## 6. 요약

| 질문 | 답 |
|---|---|
| 쿠버네티스/Argo 적용 가능? | **가능. 조건도 좋음**(Dockerfile·compose 청사진·DB 내장 큐) |
| AWS 써야 하나? | **아니오.** 이미 GCP 사용 중 → **GKE** |
| 어떤 Argo? | 학습 선택은 **Argo CD**(GitOps). 파이프라인 실행이 목적이면 Argo Workflows |
| 비용? | $300 크레딧으로 커버. 쓸 때만 켜면 월 몇 달러 |
| 주된 작업 | ①이미지 푸시 ②`deploy/k8s/` 매니페스트 ③GKE 생성 ④Argo CD 설치/Application |
| 유의점 | worker 내장 데몬 env로 켜기 / scheduler 별도 Deployment 분리 |
