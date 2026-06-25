# 경량 DB 마이그레이션 러너 이미지.
# 이전에는 migrate 가 agent-worker 이미지(chromium/tesseract 포함 수백 MB)를 재사용했다.
# worker/backend 의존성 분리(Model A) 차원에서 DB 스키마 관리를 worker 이미지에서 떼어내
# 독립 유닛으로 만든다. migrate.py 는 psycopg2 와 표준 라이브러리만 의존한다.
#
# 사용:
#   docker compose run --rm db-migrate apply --seeds
#   docker compose run --rm db-migrate status
FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "psycopg2-binary>=2.9"

# database/ 만 COPY (migrations/*.sql 은 LF 로 저장됨 → checksum 안정).
COPY database/ ./database/

ENTRYPOINT ["python", "database/migrate.py"]
CMD ["status"]
