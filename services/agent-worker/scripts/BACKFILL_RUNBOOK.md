# DataLab ML 백필 런북

2021~2023 DataLab(검색 트렌드)을 Supabase에 백필하고, 주가는 로컬 CSV로 받아
모델 경연(`app.ml.research.bakeoff`)을 돌리는 전체 절차.

> **GPU 불필요·연산 초소형**이라 **로컬 실행이 권장**입니다(한국 로컬은 Supabase 서울·
> Naver와 지연이 낮아 vast.ai보다 백필이 빠름). 아래 A(로컬) 또는 B(vast.ai) 선택.

---

## A. 로컬 실행 (권장)

이미 레포 `.venv`(uv)에 필요한 패키지가 다 있음(FinanceDataReader 포함). 비밀키만 넣으면 됨.

1) 레포 루트 `.env` 에 추가:
```
DATABASE_URL=postgresql://postgres:<DB_PASSWORD>@db.zltlpcpmdooosekipgsd.supabase.co:5432/postgres
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```
(`<DB_PASSWORD>`는 Supabase → Settings → Database 의 비밀번호. 스크립트가 이 `.env`를 자동 로드.)

2) `services/agent-worker`에서 실행:
```powershell
cd services/agent-worker
# (1) 주가 CSV (키 불필요)
uv run python scripts/backfill_prices_fdr.py --start 2021-01-01 --end 2023-12-31 --out prices_2021_2023.csv
# (2) DataLab 백필 — 먼저 1년으로 양·속도 확인 후 전체
uv run python scripts/backfill_datalab.py --start-year 2021 --end-year 2021
uv run python scripts/backfill_datalab.py --start-year 2021 --end-year 2023
# (3) 모델 경연
uv run python -m app.ml.research.bakeoff --source datalab-db --start 2021-01-01 --end 2023-12-31 `
    --prices-csv prices_2021_2023.csv --benchmark KS11 --csv results.csv
```

아래 "꼭 알아둘 점"은 로컬에도 동일하게 적용됩니다.

---

## B. vast.ai 원격 서버 (대안)

갓 렌트한 vast.ai 우분투 박스 기준. (CPU 인스턴스면 충분 — GPU 불필요)

---

## 0. 미리 준비할 것

| 항목 | 어디서 | 비고 |
|---|---|---|
| **DATABASE_URL** | Supabase 프로젝트 → Settings → Database → Connection string | `postgresql://...:<pw>@db.<ref>.supabase.co:5432/postgres` |
| **NAVER_CLIENT_ID / SECRET** | developers.naver.com → 앱 등록 → "데이터랩(검색어 트렌드)" 사용 | 무료 |
| 코드 | 이 repo (아래 1단계) | 미커밋 변경이 있으면 먼저 push 또는 scp |

> ⚠️ **GPU 불필요.** 이 작업은 전부 CPU·네트워크 작업이라 vast.ai에서 가장 싼 CPU 인스턴스면 충분합니다(딥러닝 안 씀).

---

## 1. 코드 올리기 (scp/rsync — GitHub 안 건드림)

ML 하니스가 로컬 미커밋이므로 **레포 루트 전체**를 서버로 복사합니다.
⚠️ `services/agent-worker`만 보내면 안 됩니다 — uv 워크스페이스가 루트
`pyproject.toml`/`uv.lock`과 `packages/data-access`를 필요로 합니다.

```bash
# 로컬(레포 루트 C:\...\signal-alpha)에서 실행.
# vast.ai는 보통 비표준 SSH 포트를 줍니다 → -e "ssh -p <PORT>"
rsync -az --delete \
  -e "ssh -p <PORT>" \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'web/.next' \
  ./ root@<VAST_HOST>:~/signal-alpha/
```

Windows에서 rsync가 없으면 Git Bash의 rsync, WSL, 또는 `scp -P <PORT> -r ./ root@<HOST>:~/signal-alpha/`
(scp는 위 exclude가 안 되니 `.venv`/`node_modules`를 먼저 지우고 보내세요).

서버에서:
```bash
cd ~/signal-alpha
```

## 2. 환경 설치 (repo 루트에서)

```bash
bash services/agent-worker/scripts/setup_remote.sh
# (xgboost/lightgbm/catboost 도 원하면)  WITH_BOOSTERS=1 bash services/agent-worker/scripts/setup_remote.sh
export PATH="$HOME/.local/bin:$PATH"
```

## 3. 자격증명

```bash
export DATABASE_URL='postgresql://...:<pw>@db.<ref>.supabase.co:5432/postgres'
export NAVER_CLIENT_ID='...'
export NAVER_CLIENT_SECRET='...'
cd services/agent-worker
```

## 4. 주가 + KOSPI → CSV (키 불필요)

```bash
uv run python scripts/backfill_prices_fdr.py \
  --tickers 005930,000660,035420 --benchmark KS11 \
  --start 2021-01-01 --end 2023-12-31 --out prices_2021_2023.csv
```

## 5. DataLab 백필 → Supabase

```bash
# 먼저 1년만으로 검증(빠름) 후 전체 권장
uv run python scripts/backfill_datalab.py --start-year 2021 --end-year 2021
uv run python scripts/backfill_datalab.py --start-year 2021 --end-year 2023
```

- 수집 카테고리/키워드는 **DB에서 자동 도출**(이미 수집된 3종목 카테고리 28개 기준).
- 재실행 안전: 중복 관측은 skip.

## 6. 모델 경연 (진짜 데이터)

```bash
uv run python -m app.ml.research.bakeoff --source datalab-db \
  --tickers 005930,000660,035420 --start 2021-01-01 --end 2023-12-31 \
  --prices-csv prices_2021_2023.csv --benchmark KS11 \
  --csv results_2021_2023.csv
```

표본이 0이면 백필이 비었다는 뜻 → 5단계 로그(`inserted=`) 확인.

---

## 꼭 알아둘 점 / 부작용

1. **PROD DB에 대량 쓰기.** 28카테고리 × 키워드 × ~750거래일 ≈ **수만~10만 행**이
   `datalab_raw_documents`/`datalab_raw_details`에 들어갑니다. Supabase 용량·시간 고려.
   먼저 `--start-year 2021 --end-year 2021`로 양·속도를 가늠하세요.
2. **processing_queue 적재(부작용).** 수집기는 레코드마다 `NORMALIZE_DATALAB` 태스크를
   큐에 넣습니다. ML 로더는 `datalab_raw_details`를 직접 읽어 **정규화가 필요 없으므로**,
   워커를 안 띄우면 큐에 pending이 쌓일 뿐입니다(원하면 백필 후 정리 가능).
3. **Naver 호출 제한.** API는 일일 쿼터/속도 제한이 있음(클라이언트가 재시도). 호출 수는
   대략 카테고리×키워드×연도(연 단위 청크)라 수백 콜 수준 — 쿼터 내.
4. **Supabase 연결.** 직접 연결(5432) 사용. 다량 트랜잭션이 느리면 풀러(6543)나
   배치 시간 여유를 두세요.
5. **3종목 한계.** 결과는 "배관·학습 검증"이지 성능 판정이 아닙니다. 표의 표본 수·
   baseline 대비(Dbase)·fold 표준편차를 함께 보세요.

---

## 빠른 점검 (로컬에서 미리)

DB 없이 변환 파이프라인만 확인하려면:
```bash
cd services/agent-worker
uv run python -m app.ml.research.bakeoff --source datalab-demo
uv run pytest tests/test_ml_harness.py tests/test_ml_datalab_dataset.py -q
```
