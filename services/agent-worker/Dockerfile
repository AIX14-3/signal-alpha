FROM ghcr.io/astral-sh/uv:0.11.19 AS uv
FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/workspace/.venv/bin:$PATH"

# ENRICH_HIRING(#390): 채용 포스터 OCR 엔진(Tesseract). eng 는 tesseract-ocr 에 기본 포함,
# kor 만 추가한다. Debian 패키지가 /usr/share/tesseract-ocr/.../tessdata 에 설치하고 자동
# 인식하므로 TESSDATA_PREFIX 설정은 불필요. 변경이 드문 시스템 의존성이라 app COPY 보다
# 앞에 둬 레이어 캐시를 재사용한다. 미설치 시 enrich 는 graceful skip(app/enrichment/tesseract_ocr.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock .python-version ./
COPY packages/data-access ./packages/data-access
COPY services/agent-worker/pyproject.toml ./services/agent-worker/pyproject.toml
COPY packages/signal-core/pyproject.toml ./packages/signal-core/pyproject.toml
COPY packages/market-data/pyproject.toml ./packages/market-data/pyproject.toml
COPY packages/vol-models ./packages/vol-models
COPY services/main-server/pyproject.toml ./services/main-server/pyproject.toml
COPY services/agent-worker/app ./services/agent-worker/app
RUN uv sync --locked --package signal-alpha-agent-worker --no-dev

WORKDIR /workspace/services/agent-worker

# alt-data 파이프라인 CLI(수집/정규화/분석) + 실행 래퍼 — Cloud Run Job/스케줄러용.
# 평소 서비스 CMD(uvicorn)와 무관하게, Job이 `sh run_altdata_pipeline.sh`로 덮어써 1회 실행한다.
# 이 스크립트들은 app.*(위에서 설치) + 설치된 패키지만 import하므로 추가 의존성이 없다.
COPY services/agent-worker/run_collectors.py \
     services/agent-worker/run_normalizers.py \
     services/agent-worker/run_analyzers.py \
     services/agent-worker/run_altdata_pipeline.sh \
     ./

# 인스턴스 분리 엔트리포인트(collector/scheduler/단발 드레인) + 공용 부트스트랩 모듈.
# 이 이미지를 command 만 바꿔 collector(run_collector_instance) · scheduler
# (run_scheduler_instance) 유닛으로도 기동한다. 세 스크립트는 mvp_runtime 를 import 하므로
# 함께 COPY 해야 이미지에서 실행된다(미포함 시 ModuleNotFoundError).
COPY services/agent-worker/mvp_runtime.py \
     services/agent-worker/run_collector_instance.py \
     services/agent-worker/run_scheduler_instance.py \
     services/agent-worker/run_worker_drain.py \
     ./

EXPOSE 8011

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
