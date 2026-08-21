FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Production command: binds the port the platform assigns ($PORT on Railway),
# no auto-reload. Local dev overrides this in docker-compose.yml with --reload.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
