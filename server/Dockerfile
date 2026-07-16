FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APITUNER_PORT=6592 \
    APITUNER_DATA_DIR=/data

WORKDIR /app

# System deps for zeroconf/pychromecast networking are already in slim.
# curl is included so operators can diagnose Agent reachability from inside
# the container (e.g. docker exec apituner curl http://DEVICE_IP:9092/api/health).
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY apituner ./apituner

VOLUME ["/data"]
EXPOSE 6592

# Host networking is recommended (see docker-compose) so mDNS + device
# discovery works; the app binds to APITUNER_PORT on all interfaces.
CMD ["sh", "-c", "uvicorn apituner.main:app --host 0.0.0.0 --port ${APITUNER_PORT}"]
