# signal-alpha GCP 4분할 배포 — 인스턴스 사이징 분석

> 작성: 2026-06-25 · 목적: **데모·발표용 최저비용** 배포 시 어떤 GCP 인스턴스를 빌릴지 결정.
> 전제: worker / backend / frontend / db **4개 분리**, DB는 **Cloud SQL 관리형**, **CPU만(GPU off)**,
> **임베딩 제외**(임베딩 작업 미사용), 증권사 리포트는 **LLM 파싱 → ML/DL 직행**으로 전환 중(RAG/`report_chunks` 경로 폐기).

---

## 1. 핵심 결론 (요약)

| 컴포넌트 | 권장 GCP 리소스 | vCPU / RAM | 디스크 | 대략 월비용* |
|---|---|---|---|---|
| **worker** | **ML 구성에 따라 가변 — §2.1 참조** | — | 20 GB pd-balanced | $13~$49+ |
| ↳ EWMA만 | Compute Engine **e2-small** | 2 vCPU(공유) / 2 GB | 20 GB | ~$13 |
| ↳ EWMA + classical ML 2~3개 | **e2-standard-2** 또는 **e2-highcpu-2** | 2 **전용** vCPU / 8 또는 2 GB | 20 GB | ~$36~$49 |
| ↳ DL(torch) 포함 | **e2-standard-4** (또는 GPU) | 4 vCPU / 16 GB | 30 GB | ~$97+ |
| **backend** | Compute Engine **e2-small** | 2 vCPU(공유) / 2 GB | 20 GB | ~$13 |
| **frontend** | Compute Engine **e2-small** | 2 vCPU(공유) / 2 GB | 20 GB | ~$13 |
| **db** | **Cloud SQL for PostgreSQL 16** `db-g1-small` | 1 vCPU(공유) / 1.7 GB | 10 GB SSD(자동증가) | ~$25–35 |

\* us 리전 on-demand 대략치. 서울(`asia-northeast3`)은 소폭 비쌈, 24/7 가정, 약정(CUD) 미적용 기준.

> **worker 등급은 "몇 개 모델을 쓰느냐"가 아니라 "어떤 종류 모델이냐"가 결정한다.** EWMA만이면
> e2-small로 충분하지만, ML/DL 모델을 추가하면 §2.1처럼 더 좋은 인스턴스가 필요하다.

**합계(backend+frontend+db 고정 ~$51–61)에 worker를 더한 값:**
- worker **e2-small**(EWMA만): 월 **~$64–74**
- worker **e2-highcpu-2**(EWMA + classical ML): 월 **~$87–97**
- worker **e2-standard-4**(DL 포함): 월 **~$148–158** (+DL이 GPU면 별도 상승)

> 임베딩 제거로 worker 메모리 바닥선이 ~2–3GB → ~1GB로 내려가 **EWMA만이면 e2-small까지** 가능해졌다.
> 단, ML/DL 모델을 얹으면 CPU(또는 DL이면 메모리·GPU) 요구가 다시 올라가므로 §2.1 기준으로 등급을 정한다.

---

## 2. 컴포넌트별 리소스 풋프린트 근거

### 2.1 ⭐ worker ML/DL 모델 사이징 (등급을 가르는 핵심)

worker 인스턴스 등급은 어떤 변동성 모델을 in-process로 돌리느냐가 결정한다. 모델 카탈로그는
`packages/vol-models/vol_models/models/`, 게이트는 `services/agent-worker/app/ml/model_registry.py`.

**모델 종류별 실제 부하(코드 기준):**

- **classical CPU 모델** (`ewma`, `har_rv`, `garch`, `lightgbm` — 기본 게이트 통과 세트):
  - LightGBM은 **추론할 때마다 300트리를 `n_jobs=-1`로 학습**한다(`cpu_lgbm.py:72-82` — 가용 코어 전부 점유).
    GARCH(`arch`)도 종목마다 fit. → **메모리는 가벼움(fit이 일시적, 수백 MB 이하)**.
  - **진짜 병목은 CPU.** 종목 루프로 배치 추론하면 CPU 바운드가 된다.
- **DL 모델** (`chronos2`, `kronos` — torch 기반, 기본 off):
  - `_PIPE = None` **모듈 싱글톤으로 한 번 로드 후 프로세스 내내 상주**한다(`gpu_chronos2.py:27,41-49`).
    torch 런타임(300–500MB) + 파운데이션 모델 가중치(수백 MB~GB)가 메모리에 계속 남음.
  - 2~3개 상주 시 누적 2–4GB+. CPU 추론은 매우 느림(원래 GPU용).

**시나리오별 권장 worker 인스턴스:**

| 시나리오 | 메모리 | CPU | 권장 인스턴스 | 월 대략 |
|---|---|---|---|---|
| **EWMA만** | ~1 GB | 낮음 | **e2-small** (2 vCPU공유/2GB) | ~$13 |
| **EWMA + classical ML 2~3개** (har_rv/garch/lightgbm) | ~1–1.5 GB(일시적) | **높음** | **e2-highcpu-2**(2 전용 vCPU/2GB, 가성비) 또는 **e2-standard-2**(2 vCPU/8GB) | ~$36 / ~$49 |
| **+ DL(torch, CPU)** | **상주 2–4 GB+** | 매우 높음·느림 | **e2-standard-4**(4 vCPU/16GB) | ~$97 |
| **+ DL(GPU)** | GPU VRAM 4–8GB | — | **g2-standard-4 + NVIDIA L4** (또는 T4) | 별도 산정 |

**판단 규칙:**
1. **e2-micro/small/medium은 전부 공유코어 버스트**라 CPU 크레딧 소진 시 스로틀된다. ML을 종목별로
   **지속 배치 추론**하면 메모리가 남아도 **전용 vCPU(e2-highcpu-2 / e2-standard-2 이상)** 가 맞다.
2. classical만이면 메모리 부담이 작으므로 **e2-highcpu-2(2 전용 vCPU/2GB)** 가 비용·성능 균형 최적.
3. **DL(torch)을 켜면** 메모리·속도 둘 다 부담 → e2-small은 확실히 부족. CPU 추론은 느려서 데모라도
   GPU 인스턴스를 고려해야 한다(앞서 "GPU off" 전제를 DL 사용 시 재검토 필요).
4. 이 worker엔 **채용 크롤러 Chromium**(크롤 시 +300–500MB 스파이크)도 공존한다. ML 배치와 크롤이
   겹치면 여유가 더 필요하며, 부하가 크면 **크롤러 worker와 ML 추론 worker를 분리**하는 것도 선택지다.

### worker (`services/agent-worker`)
임베딩 제외 시 메모리 프로파일이 크게 가벼워진다.

- **BGE-M3 임베딩 모델 제거**: `app/embeddings/provider.py`의 BGE-M3는 `get_embedding_provider()`가
  처음 호출될 때만 로드되는 **지연 로딩**이다(시작 시 상주 아님). 호출 지점은 EMBED_REPORT·EMBED_DART
  핸들러와 리포트 RAG 검색기 3곳뿐 → 임베딩 작업을 안 돌리면 모델(~2–3GB)이 **아예 로드되지 않음**.
- **torch 의존성 소거**: 현재 torch는 `sentence-transformers`(agent-worker 의존성)를 통해 들어온다.
  임베딩을 빼면서 이미지에서 `sentence-transformers`를 제거하면 **torch도 함께 빠진다**
  (vol-models base는 numpy/pandas만, GPU extra는 off). → worker 베이스 ~1GB.
- **상시/주기 부하 (여전히 존재)**:
  - 가격 수집 데몬(키움 REST, `PRICE_COLLECTOR_ENABLED=true`) — 메모리 미미, I/O 바운드.
  - 채용 크롤러: **Chromium headless + Selenium + Tesseract OCR**(이미지에 내장). 크롤 시
    Chromium이 순간 300–500MB+ 점유 → **메모리 스파이크의 주범**.
  - PDF 파싱(pymupdf): PDF 크기에 따라 순간 100–300MB.
  - LLM 호출(Gemini/OpenAI): **API 호출**이라 온디바이스 추론 없음(메모리 영향 적음).
  - ML/DL 추론(vol-models, `app/ml/`): CPU 기본 모델(EWMA/HAR-RV)은 numpy/pandas로 가벼움.
- **결론**: 평상시 ~1GB, 크롤/PDF 동시 발생 시 순간 1.5–2GB. **e2-small(2GB)이 타깃**,
  스파이크 여유가 필요하면 **e2-medium(4GB)**.

> ⚠️ **분기 주의 — 리포트 ML/DL 전환**: 새 "LLM 파싱 → ML/DL 직행" 경로의 DL 추론이
> **CPU torch나 무거운 모델을 in-process로 로드**한다면 worker 메모리가 +0.5–1.5GB 늘어난다.
> 이 경우 worker는 **e2-medium(4GB) 이상** 권장. DL이 lightgbm/sklearn/numpy 수준이면 e2-small 유지 가능.
> → **이 경로 확정 후 worker 등급을 최종 결정**할 것.

### backend (`services/main-server`)
- Postgres 위 **얇은 FastAPI**(단일 uvicorn). 요청 핸들러에 무거운 연산 없음(임베딩/RAG는 backend 아님).
- ~256–512MB. **e2-small(2GB)** 로 충분(여유 큼). 비용 더 줄이려면 frontend와 합치는 것도 가능(§5).

### frontend (`web`)
- Next.js 15 **SSR(Node)**. compose는 현재 **dev 서버**로 띄우므로 프로드는
  `next build && next start`(또는 `output:'standalone'`)로 전환 필요 — `web/Dockerfile` 수정 포인트.
- `next build`가 1–2GB를 쓰므로 **2GB는 있어야 빌드 OOM 회피**. 런타임 서빙은 512MB–1GB.
- **e2-small(2GB)**. (빌드를 CI/로컬에서 하고 산출물만 올리면 e2-micro도 가능하나 데모엔 e2-small 권장.)

### db (Cloud SQL for PostgreSQL 16)
- 이미지: `pgvector/pgvector:pg16` → Cloud SQL에서 **pgvector 확장 enable**.
- 임베딩을 빼도 `report_chunks`/`dart_chunks` **테이블·확장 자체는 스키마에 남음**(데이터만 안 쌓임).
  벡터 적재가 없으면 DB 메모리/디스크 압박이 더 작아짐 → 데이터 ~수백 MB 수준.
- OLTP 위주(인덱스 조회·배치 insert), 무거운 분석 쿼리 없음. 풀 크기 main-server 10 / worker 20 내외.
- **`db-g1-small`(1.7GB)** 로 데모 충분. 백업·SSL·패치 자동.

---

## 3. 네트워크/토폴로지 (권장)

- 리전: **`asia-northeast3`(서울)** — 한국 사용자 지연시간 최소.
  (무료 등급 e2-micro는 us-west1/central1/east1만 → 서울과 트레이드오프, §5)
- VPC 1개 + 서브넷. 방화벽:
  - **외부 노출**: frontend `:3000`, backend `:8000` (또는 앞단 LB/HTTPS).
  - **내부 전용**: worker `:8011`, Cloud SQL.
- **Cloud SQL은 Private IP**로 VPC에 연결, worker/backend만 접근 허용. 접속 문자열 `sslmode=require`
  (참고: DB SSL 단일화 PR #443, `COLLECTOR_DB_SSLMODE`).

---

## 4. 배포 순서 & 환경변수

1. GCP 프로젝트/네트워크/방화벽 구성.
2. **Cloud SQL** 생성(PG16, db-g1-small, Private IP, 백업 on) → `CREATE EXTENSION vector;`
   → `database/migrations` 적용(기존 `migrate.py`/migrate 컨테이너, **SQL은 LF**, 드리프트 확인).
3. **worker** VM: Docker로 agent-worker 이미지 구동.
   - 핵심 env: `DATABASE_URL`(Cloud SQL Private IP, sslmode=require), `GEMINI_API_KEY`,
     `PRICE_COLLECTOR_ENABLED`, 키움 **모의키**(`KIWOOM_API_BASE=https://mockapi.kiwoom.com`),
     GPU 모델 비활성 유지(`ML_GATE_PASSED_MODELS` 기본).
   - 임베딩 제외 운영: EMBED_DART/EMBED_REPORT 작업을 enqueue하지 않도록 운영
     (코드 게이트는 진행 중인 리팩터링에서 처리 예정 — 본 문서는 인프라 한정).
4. **backend** VM: main-server 이미지 구동(`DATABASE_URL` 동일).
5. **frontend** VM: 프로드 빌드 이미지 구동, `NEXT_PUBLIC_*` API base를 backend 주소로 설정.

기동 순서: **Cloud SQL → 마이그레이션 → worker → backend → frontend**.

---

## 5. 비용을 더 낮추는 레버 (선택)

1. **backend + frontend 1대 합치기**: 둘 다 가벼워 e2-small 1대에 함께 올리면 VM 1대(~$13) 절감.
   ("4분할" 요구와 배치되나 데모면 합리적.)
2. **frontend 빌드 외부화**: `next build`를 CI/로컬에서 수행하고 산출물만 배포하면 frontend를
   e2-micro(1GB)까지 낮출 여지(무료 등급 가능 리전 한정).
3. **무료 등급**: e2-micro 1대 월 무료(us 리전). backend 정도는 수용 가능하나 1GB라 frontend 빌드·worker엔 부족.
4. **Cloud Run 대안(backend/frontend)**: stateless라 idle 시 0 스케일 → 트래픽 적은 데모에 더 저렴할 수 있음.
   단 **worker는 가격 수집 데몬이 상시 동작**하므로 min-instances=1 + always-on CPU 필요 → VM과 큰 차이 없음.

---

## 6. 검증 방법

- **DB**: `SELECT extversion FROM pg_extension WHERE extname='vector';` 확인, 마이그레이션 테이블 적재 확인.
- **worker**: 컨테이너 로그에 **BGE-M3 로딩 로그가 없는지**(임베딩 미적재 확인), 가격 수집 데몬 동작 확인.
  `free -m`으로 평상시·크롤 중 메모리가 인스턴스 한도 내인지(특히 e2-small 선택 시 스파이크 OOM 여부).
- **backend**: `curl http://<backend>:8000/health`, 주요 API(`/api/signals`)가 Cloud SQL에서 응답하는지.
- **frontend**: 브라우저 `http://<frontend>:3000` 접속, 홈/리포트가 backend API와 연동되는지.
- **E2E**: 1종목으로 프론트→백엔드→Cloud SQL 경로 + worker 적재 반영 스모크.
- **비용**: 배포 1–2일 후 GCP 청구 대시보드에서 실제 비용이 추정 범위인지 확인.

---

## 7. 한 줄 요약

임베딩 제외 전제에서 **backend = e2-small**, **frontend = e2-small**, **db = Cloud SQL db-g1-small(pgvector)** 는 고정.
**worker만 ML 구성으로 가변**:
- **EWMA만 → e2-small** (메모리·CPU 모두 가벼움)
- **EWMA + classical ML 2~3개(garch/lightgbm 등) → e2-highcpu-2 / e2-standard-2** (메모리는 작아도
  lightgbm/garch가 추론마다 fit하며 CPU를 점유 → **전용 vCPU 필요**)
- **DL(torch) 포함 → e2-standard-4 이상, 현실적으로 GPU(g2+L4/T4)** (모델이 상주하며 메모리·속도 둘 다 부담)

즉, "모델을 몇 개 쓰느냐"가 아니라 **classical(CPU 바운드)이냐 DL(메모리·GPU 바운드)이냐**가 worker 등급을 가른다.
**최종 등급은 실제로 켤 모델 목록(특히 torch DL 포함 여부)을 확정한 뒤 §2.1 표로 결정**할 것.
