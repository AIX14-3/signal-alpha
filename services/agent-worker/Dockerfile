FROM python:3.13-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY packages/data-access ./packages/data-access
COPY services/agent-worker/pyproject.toml ./services/agent-worker/pyproject.toml
COPY services/agent-worker/app ./services/agent-worker/app
RUN python -m pip install --no-cache-dir \
    -e ./packages/data-access \
    -e ./services/agent-worker

WORKDIR /workspace/services/agent-worker

EXPOSE 8011

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
