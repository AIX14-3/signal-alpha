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
COPY services/price-collector/pyproject.toml ./services/price-collector/pyproject.toml
COPY services/price-collector/app ./services/price-collector/app
RUN uv sync --locked --package signal-alpha-price-collector --no-dev

WORKDIR /workspace/services/price-collector

CMD ["python", "-m", "app.main"]
