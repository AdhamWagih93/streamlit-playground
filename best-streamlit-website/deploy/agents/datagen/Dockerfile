FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY deploy/agents/datagen/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "src.ai.agents.services.datagen_api:app", "--host", "0.0.0.0", "--port", "8080"]
