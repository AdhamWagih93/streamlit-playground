FROM python:3.11-slim

WORKDIR /app

# Docker SDK talks to the Docker Engine API; the CLI is not required.
COPY deploy/mcp-docker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["python", "-m", "src.ai.mcp_servers.docker.mcp"]
