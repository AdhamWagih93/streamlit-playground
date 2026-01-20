FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG HELM_VERSION=v3.14.4

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl tar \
  && rm -rf /var/lib/apt/lists/*

# Install Helm (server-side CLI)
RUN curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" -o /tmp/helm.tgz \
  && tar -xzf /tmp/helm.tgz -C /tmp \
  && mv /tmp/linux-amd64/helm /usr/local/bin/helm \
  && chmod +x /usr/local/bin/helm \
  && rm -rf /tmp/helm.tgz /tmp/linux-amd64

COPY deploy/mcp-helm/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Default is stdio; K8s/Helm should set HELM_MCP_TRANSPORT=http and expose port 8000.
CMD ["python", "-m", "src.ai.mcp_servers.helm.mcp"]
