# Build from the questops/ directory:  docker build -f backend/Dockerfile .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./frontend

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -sf http://localhost:8080/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
