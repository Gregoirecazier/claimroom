FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.12 'uvicorn[standard]==0.34.2' gradium==0.6.4
COPY apps/api/claim_api/gradium_bridge.py /app/gradium_bridge.py
COPY apps/voice-bridge/main.py /app/main.py
COPY apps/voice-bridge/media_worker.py /app/media_worker.py

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log --log-level warning"]
