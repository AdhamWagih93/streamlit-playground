FROM python:3.11-slim

WORKDIR /app

COPY deploy/mcp-local/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000

CMD ["python", "-m", "src.ai.mcp_servers.local.mcp"]
