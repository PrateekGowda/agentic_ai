FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/orchestrator/pyproject.toml /app/services/orchestrator/pyproject.toml
COPY services/orchestrator/src /app/services/orchestrator/src
COPY agents /app/agents
COPY samples /app/samples
COPY templates /app/templates

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir /app/services/orchestrator

ENV PYTHONPATH=/app/agents
EXPOSE 8000

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/services/orchestrator/src"]
