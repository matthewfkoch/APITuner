# APITuner Server

FastAPI application that powers the virtual tuner: dashboard, REST API,
HDHomeRun emulation, M3U playlist, tuner orchestration, and HDMI encoder
stream relay.

## Run locally

```bash
cd server
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
APITUNER_DATA_DIR=../data uvicorn apituner.main:app --reload --port 6592
```

- Dashboard: http://localhost:6592
- OpenAPI docs: http://localhost:6592/docs
- HDHomeRun discover: http://localhost:6592/discover.json
- M3U playlist: http://localhost:6592/channels.m3u
- XMLTV guide: http://localhost:6592/xmltv.xml

## Tests

```bash
cd server
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Layout

| Module | Purpose |
| ------ | ------- |
| `main.py` | FastAPI app wiring and core REST routes |
| `tuner_manager.py` | Tuner pool, tune orchestration, readiness |
| `backends/` | `http_agent`, `androidtv_remote`, `firetv_rest`, `adb`, hybrid `SplitControlBackend` |
| `config_interpreter.py` | ADBTuner / babsonnexus App Play command runner (Remote keys or ADB shell) |
| `dynamic_url.py` | FruitDeepLinks / lane deeplink URL resolution before launch |
| `whos_watching.py` | Encoder frame OCR + timed DPAD for profile prompts |
| `preview.py` | Encoder MJPEG/JPEG frame grab for dashboard Preview |
| `packages.py` / `package_coverage.py` | Installed-app matching + dashboard package warnings |
| `keys.py` | `key_macro` normalization (`;` / `,` / `KEYCODE_`) |
| `hdhr/` | HDHomeRun discovery, lineup, stream routes, XMLTV |
| `playlist.py` | M3U / M3U8 generation for Channels DVR |
| `stream.py` | MPEG-TS proxy / redirect |
| `config.py` | Persistent `config.json` + ADBTuner channel/config import/export |
| `discovery.py` | mDNS discovery for Agent / Android TV Remote |
| `adb_grant.py` | One-time Fire TV Agent permission grant via network ADB |
| `web/` | Dashboard static assets |

Runtime data (config, pairing certs) lives under `APITUNER_DATA_DIR` (default
`./data` relative to the working directory, or `/data` in Docker).
