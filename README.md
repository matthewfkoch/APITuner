# APITuner

An **ADB-free** virtual tuner for [Channels DVR](https://getchannels.com/), in the spirit of ADBTuner. APITuner controls networked Android TV / Google TV devices through pluggable, ADB-free control backends and relays each device's paired HDMI-encoder stream to Channels as a Custom Channels (M3U) source.

- **No ADB. No root. No developer mode.**
- **Default backend:** the Android TV Remote protocol v2 (the same protocol Google's TV remote app uses) via [`androidtvremote2`](https://github.com/tronikos/androidtvremote2), with optional [`pychromecast`](https://github.com/home-assistant-libs/pychromecast) for real playback detection.
- **Secondary backend:** the bundled **APITuner Agent** APK (derived from [DisplayLauncher](https://github.com/mouldybread/DisplayLauncher)) for Fire TV and app management.
- Runs in Docker. Web dashboard on port **5593**.

> APITuner still requires an **HDMI encoder** per device (like ADBTuner). Streaming apps are DRM-protected, so a device cannot screen-capture itself; the encoder captures the device's HDMI output and serves it as MPEG-TS.

---

## How it works

```
Channels DVR ──GET /stream/9000──▶ APITuner ──control──▶ Android TV device
                                      │                     (launches app / deep link)
                                      └──relay MPEG-TS◀── HDMI encoder ◀─HDMI─ device
```

1. Channels DVR requests a channel from APITuner's `channels.m3u`.
2. APITuner picks a free, eligible tuner and tells its device to launch the channel's app / deep link.
3. It waits until the app is playing (playback state, foreground app, or a fixed delay depending on the backend), optionally sends a key macro to clear prompts.
4. It relays the paired HDMI encoder's MPEG-TS to Channels and releases the tuner when the stream ends.

## Control backends

| Capability      | `androidtv_remote` (Google TV) | `http_agent` (Agent APK) |
| --------------- | ------------------------------ | ------------------------ |
| Launch/deeplink | ✅                             | ✅                       |
| Key events      | ✅ full D-pad                  | ⚠️ BACK/HOME/RECENTS     |
| Foreground app  | ✅                             | ✅ (Usage Access)        |
| Playback state  | ✅ (via Cast)                  | ✅ (Notification Access) |
| Power           | ✅                             | ❌                       |
| App list/install| ❌                             | ✅                       |
| Force-stop      | ❌ (go HOME)                   | ❌ (go HOME)             |

Pick the backend per tuner. Use `androidtv_remote` for Chromecast with Google TV / Android TV; use `http_agent` for Fire TV or when you need the app list / installer.

---

## Quick start (Docker)

```bash
git clone <this-repo> APITuner
cd APITuner
docker compose up -d --build
```

Open the dashboard at `http://<docker-host>:5593`.

To use mDNS **Discover**, enable host networking (see the comment in `docker-compose.yml`); otherwise add tuners by IP manually.

### Run without Docker

```bash
cd server
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn apituner.main:app --host 0.0.0.0 --port 5593
```

---

## Set up a tuner

1. **Add a tuner** in the dashboard (or click **Discover**).
2. Choose the backend:
   - `androidtv_remote`: enter the device IP. Then click **Pair** — a PIN appears on the TV; enter it. The pairing certificate is stored under `data/certs/`.
   - `http_agent`: install the **APITuner Agent** APK (see `agent/`), then enter the device IP (port `9092`).
3. Enter the **encoder stream URL** — the HDMI encoder's MPEG-TS endpoint for that device, e.g. `http://192.168.1.41:8090/stream0`.

## Configure channels

Add channels manually or **Import** an ADBTuner channel-list JSON (the schema is compatible). Each channel has:

- `package_name` (+ optional `alternate_package_name`)
- `url` — deep link (intent data), e.g. `https://tv.youtube.com/watch/...`
- `action` (default `android.intent.action.VIEW`)
- `component` — explicit activity (used by the Agent backend; Android 12+)
- `key_macro` — keys sent after launch to dismiss prompts (remote backend only)
- `compatibility_mode`, `tvc_guide_stationid`

## Connect to Channels DVR

In Channels DVR: **Settings → Add Source → Custom Channels → M3U URL** and paste the URL shown at the top of the dashboard:

```
http://<docker-host>:5593/channels.m3u
```

## Global options

Configurable in the dashboard: tune timeout, wait-for-playback, stop-on-release, retry-on-other-tuner, release grace, stuck-tuner timeout, and **stream mode**:

- `proxy` (default, like ADBTuner) — APITuner relays the encoder stream and releases the tuner on disconnect.
- `redirect` — Channels connects to the encoder directly (max scale; tuner reclaimed after an idle timeout).

---

## Repository layout

- `server/` — the APITuner FastAPI service (dashboard, M3U, backends, orchestrator, encoder relay).
- `agent/` — the APITuner Agent Android app (secondary `http_agent` backend). CI builds the APK (`.github/workflows/agent-build.yml`).

## Accepted trade-offs

- No true force-stop on any backend (Android has no non-privileged force-stop). APITuner navigates HOME and can send a `key_macro` to handle prompts. Cleanest results come from apps with direct deep links.
- `androidtv_remote` requires the pre-installed Android TV Remote Service (present on Google TV / Android TV, absent on Fire TV → use `http_agent`).
- The Remote v2 protocol is community-maintained (Google's own protocol; stable via `androidtvremote2`).

## Licensing

- The `agent/` app is derived from **DisplayLauncher** (Apache-2.0). It keeps the Apache-2.0 `LICENSE` and a `NOTICE` crediting [mouldybread/DisplayLauncher](https://github.com/mouldybread/DisplayLauncher) and the fork [matthewfkoch/DisplayLauncher](https://github.com/matthewfkoch/DisplayLauncher), re-branded to `com.apituner.agent`.
- `androidtvremote2` and `pychromecast` are used as standard PyPI dependencies under their respective licenses.
