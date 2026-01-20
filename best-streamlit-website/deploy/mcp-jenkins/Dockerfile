FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY deploy/mcp-jenkins/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# NOTE: This MCP server runs over stdio by default.
# In Kubernetes, stdio MCP servers typically require an in-pod client.
CMD ["python", "-m", "src.ai.mcp_servers.jenkins.mcp"]
