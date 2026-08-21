FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# --reload gives live-reload during dev; drop it for a production image.
# --timeout-graceful-shutdown caps the drain: without it, a reload during a
# big enrich-all batch waits for EVERY queued background task and the API
# looks dead for minutes. Interrupted lots are reset to pending on startup.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--timeout-graceful-shutdown", "5"]
