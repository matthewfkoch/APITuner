FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APITUNER_PORT=6592 \
    APITUNER_DATA_DIR=/data

WORKDIR /app

# System deps for zeroconf/pychromecast networking are already in slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apituner ./apituner

VOLUME ["/data"]
EXPOSE 6592

# Host networking is recommended (see docker-compose) so mDNS + device
# discovery works; the app binds to APITUNER_PORT on all interfaces.
CMD ["sh", "-c", "uvicorn apituner.main:app --host 0.0.0.0 --port ${APITUNER_PORT}"]
