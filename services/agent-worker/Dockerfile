FROM ghcr.io/astral-sh/uv:0.11.19 AS uv
FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/workspace/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml uv.lock .python-version ./
COPY packages/data-access ./packages/data-access
COPY services/agent-worker/pyproject.toml ./services/agent-worker/pyproject.toml
COPY packages/signal-core/pyproject.toml ./packages/signal-core/pyproject.toml
COPY services/main-server/pyproject.toml ./services/main-server/pyproject.toml
COPY services/agent-worker/app ./services/agent-worker/app
RUN uv sync --locked --package signal-alpha-agent-worker --no-dev

WORKDIR /workspace/services/agent-worker

EXPOSE 8011

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
