FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APITUNER_PORT=6592 \
    APITUNER_DATA_DIR=/data

WORKDIR /app

# curl: diagnose Agent reachability from inside the container.
# adb: one-time Fire TV Agent permission grant (see README — day-to-day tuning is ADB-free).
# ffmpeg + tesseract: encoder frame OCR for Max / who's-watching and Auto-pair PIN reads.
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl adb ffmpeg tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY apituner ./apituner

VOLUME ["/data"]
EXPOSE 6592

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${APITUNER_PORT}/api/status" >/dev/null || exit 1

# Host networking is recommended (see docker-compose) so mDNS + device
# discovery works; the app binds to APITUNER_PORT on all interfaces.
# Use exec so uvicorn runs as PID 1 and receives SIGTERM for clean shutdown.
CMD ["sh", "-c", "exec uvicorn apituner.main:app --host 0.0.0.0 --port ${APITUNER_PORT}"]
