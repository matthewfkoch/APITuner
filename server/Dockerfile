FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APITUNER_PORT=6592 \
    APITUNER_DATA_DIR=/data

WORKDIR /app

# curl: diagnose Agent reachability from inside the container.
# adb: one-time Fire TV Agent permission grant (see README — day-to-day tuning is ADB-free).
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl adb \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY apituner ./apituner

VOLUME ["/data"]
EXPOSE 6592

# Host networking is recommended (see docker-compose) so mDNS + device
# discovery works; the app binds to APITUNER_PORT on all interfaces.
CMD ["sh", "-c", "uvicorn apituner.main:app --host 0.0.0.0 --port ${APITUNER_PORT}"]
