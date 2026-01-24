FROM python:3.11-slim

WORKDIR /app

# Minimal OS deps (certs) + build essentials are intentionally avoided.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY deploy/scheduler/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the scheduler code + shared src code it depends on.
COPY src /app/src

ENV PYTHONUNBUFFERED=1

# Default network transport for K8s.
ENV SCHEDULER_MCP_TRANSPORT=http
ENV SCHEDULER_MCP_HOST=0.0.0.0
ENV SCHEDULER_MCP_PORT=8010

EXPOSE 8010

CMD ["python", "-m", "src.scheduler.main"]
