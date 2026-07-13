# APITuner Server

FastAPI application that powers the virtual tuner: dashboard, REST API, M3U
playlist, tuner orchestration, and HDMI encoder stream relay.

## Run locally

```bash
cd server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
APITUNER_DATA_DIR=../data uvicorn apituner.main:app --reload --port 6592
```

- Dashboard: http://localhost:6592
- OpenAPI docs: http://localhost:6592/docs
- M3U playlist: http://localhost:6592/channels.m3u

## Tests

```bash
cd server
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Layout

| Module | Purpose |
| ------ | ------- |
| `main.py` | FastAPI routes |
| `tuner_manager.py` | Tuner pool, tune orchestration, readiness |
| `backends/` | `http_agent` and `androidtv_remote` control planes |
| `playlist.py` | M3U generation for Channels DVR |
| `stream.py` | MPEG-TS proxy / redirect |
| `config.py` | Persistent `config.json` + ADBTuner import/export |
| `web/` | Dashboard static assets |

Runtime data (config, pairing certs) lives under `APITUNER_DATA_DIR` (default
`./data` relative to the working directory, or `/data` in Docker).
