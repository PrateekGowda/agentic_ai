FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn
COPY agents /app/agents

ENV PYTHONPATH=/app/agents
EXPOSE 8080
CMD ["python", "-m", "common.runtime"]
