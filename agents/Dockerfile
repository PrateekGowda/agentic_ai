FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY agents /app/agents

ENV PYTHONPATH=/app/agents
CMD ["python", "-m", "common.runtime"]
