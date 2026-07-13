# Contributing

Thanks for helping improve APITuner.

## Development setup

**Server (Docker):**

```bash
docker compose up -d --build
```

**Server (local Python 3.11+):**

```bash
cd server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
APITUNER_DATA_DIR=../data uvicorn apituner.main:app --reload --port 6592
```

**Agent APK:**

```bash
cd agent
./gradlew assembleDebug
```

## Pull requests

1. Branch from `main`
2. Keep changes focused
3. Update README/docs when behavior changes
4. CI must pass (`Server CI`, `Build APITuner Agent APK`)

Run tests:

```bash
cd server && pip install -r requirements.txt -r requirements-dev.txt && pytest
```

## What not to commit

- `data/` — runtime config, pairing certificates, local tuner/channel lists
- `*.apk`, `agent/app/build/`, keystores
- Private IPs or credentials in examples (use RFC 5737 ranges like `192.0.2.x` in samples)

## Releases

Maintainers tag `v*` releases to publish Docker images and Agent APKs. See README **Releases**.
