# APITuner

An **ADB-free** virtual tuner for [Channels DVR](https://getchannels.com/), in the spirit of ADBTuner. APITuner controls networked Android TV / Google TV devices through pluggable, ADB-free control backends and relays each device's paired HDMI-encoder stream to Channels as a Custom Channels (M3U) source.

- **No ADB. No root. No developer mode.**
- **Default backend:** the bundled **APITuner Agent** APK (derived from [DisplayLauncher](https://github.com/mouldybread/DisplayLauncher)) — package-pinned deep links that work reliably with YouTube TV and other streaming apps.
- **Alternate backend:** the Android TV Remote protocol v2 via [`androidtvremote2`](https://github.com/tronikos/androidtvremote2), with optional [`pychromecast`](https://github.com/home-assistant-libs/pychromecast) for playback detection. Simpler setup (pair once, no APK) but **cannot pin the target app** on deep links, which often triggers Android's "Open with" chooser.
- Runs in Docker. Web dashboard on port **6592**.

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

| Capability       | `http_agent` (Agent APK) **recommended** | `androidtv_remote` (Google TV Remote) |
| ---------------- | ---------------------------------------- | ------------------------------------- |
| Launch/deeplink  | ✅ package-pinned (reliable)              | ⚠️ bare URL only (app chooser risk)   |
| Key events       | ⚠️ BACK/HOME/RECENTS                     | ✅ full D-pad                         |
| Foreground app   | ✅ (Usage Access)                         | ✅                                    |
| Playback state   | ✅ (Notification Access)                  | ✅ (via Cast, LAN-dependent)          |
| App list/install | ✅                                        | ❌                                    |
| Setup            | Install APK + 2 permissions              | Pair once, no APK                     |
| Best for         | **YouTube TV, Google TV, Fire TV**       | Simple key-only control, no APK       |

**Use `http_agent` for production tuning** (especially YouTube TV). The Agent sends intents with an explicit package, like ADBTuner's `am start`, so channels open directly instead of stalling on an "Open with" dialog.

Use `androidtv_remote` only when you cannot install the Agent APK.

### Agent setup (per device)

1. Install the APK from [GitHub Releases](https://github.com/matthewfkoch/APITuner-releases/releases) (or build from `agent/`).
2. Open the app and grant:
   - **Display over other apps** — **required** (allows background app launches)
   - **Usage Access** — **recommended** (foreground-app tune readiness)
   - **Notification Access** — optional (playback-state detection)
   - **Accessibility** — optional (BACK/HOME/RECENTS keys)
3. In the APITuner dashboard, add a tuner with backend `http_agent`, device IP, port `9092`, and the HDMI encoder stream URL.

---

## Quick start (Docker)

### From GitHub Container Registry (releases)

```bash
mkdir -p apituner-data
docker run -d \
  --name apituner \
  -p 6592:6592 \
  -v "$(pwd)/apituner-data:/data" \
  --restart unless-stopped \
  ghcr.io/matthewfkoch/apituner:latest
```

### Build locally

```bash
git clone https://github.com/matthewfkoch/APITuner.git
cd APITuner
docker compose up -d --build
```

Open the dashboard at `http://<docker-host>:6592`.

To use mDNS **Discover**, enable host networking (see the comment in `docker-compose.yml`); otherwise add tuners by IP manually.

### Run without Docker

```bash
cd server
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
APITUNER_DATA_DIR=../data uvicorn apituner.main:app --host 0.0.0.0 --port 6592
```

Config and pairing certs are stored under `APITUNER_DATA_DIR` (default `./data` relative to the working directory). Docker Compose mounts `./data` at the repo root — use the same path when running locally to share config.

---

## Set up a tuner

1. **Add a tuner** in the dashboard (or click **Discover**).
2. **Recommended — `http_agent`:** install the Agent APK (see above), grant permissions, enter the device IP (port `9092`).
3. **Alternate — `androidtv_remote`:** enter the device IP, click **Pair**, enter the PIN shown on the TV. Pairing certs are stored under `data/certs/`.
4. Enter the **encoder stream URL** — the HDMI encoder's MPEG-TS endpoint, e.g. `http://192.168.1.200/4.ts`.

See `config.example.json` for a sample configuration.

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
http://<docker-host>:6592/channels.m3u
```

## Global options

Configurable in the dashboard:

| Option | Description |
| ------ | ----------- |
| Tune timeout | Max seconds to wait for a channel to become ready |
| Wait for playback | Prefer playback-state signal before accepting tune |
| Stop on release | Send HOME when the stream ends |
| Keep apps running | When off, always send HOME on release (overrides keep-warm behavior) |
| Retry on other tuner | Try another eligible tuner if a tune fails |
| Request timeout | HTTP timeout for Agent API calls (seconds) |
| Stream mode | `proxy` (default, like ADBTuner) or `redirect` (Channels hits encoder directly) |
| Release grace | Seconds to hold tuner lock after stream disconnect |
| Stuck / idle timeouts | Reclaim tuners that stop making progress |

- `proxy` (default) — APITuner relays the encoder stream and releases the tuner on disconnect.
- `redirect` — Channels connects to the encoder directly (lower server load; tuner reclaimed after idle timeout).

---

## Repository layout

- `server/` — the APITuner FastAPI service. See [server/README.md](server/README.md) for dev setup, tests, and API docs (`/docs`).
- `agent/` — the APITuner Agent Android app (`http_agent` backend). CI builds the APK (`.github/workflows/agent-build.yml`).
- `config.example.json` — sample tuners, channels, and options.
- `CHANGELOG.md` — version history.

## Troubleshooting

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| Tune times out, TV shows "Open with" | `androidtv_remote` backend | Switch to `http_agent` and install the Agent APK |
| Agent launch succeeds but app doesn't open | Missing "Display over other apps" | Grant overlay permission on the device |
| Same-app channel switch times out | Usage Access not granted | Grant Usage Access; use latest server |
| Discover finds nothing in Docker | Bridge network blocks mDNS | Use `network_mode: host` or add tuners manually |
| `androidtv_remote` playback never ready | Cast/mDNS unreachable from Docker | Use Agent backend, or host networking |
| No free tuner | All tuners locked | Wait for stream to end, or lower idle/stuck timeouts |

## Security

The dashboard and API on port **6592** are **not authenticated**. Do not expose APITuner to the public internet. See [SECURITY.md](SECURITY.md).

## Releases

Tagged releases (`v*`) trigger `.github/workflows/release.yml`, which:

1. **Publishes the server** to GitHub Container Registry: `ghcr.io/matthewfkoch/apituner:<version>` (make the GHCR package **public** once in package settings)
2. **Builds the Agent APK** and attaches it to the public [APITuner-releases](https://github.com/matthewfkoch/APITuner-releases/releases) repo (source stays private)

To cut a release:

```bash
git tag v0.1.2
git push origin v0.1.2
```

Between releases, debug APK artifacts are available from the **Build APITuner Agent APK** workflow on `main` (repo collaborators only).

### Public APK releases (private source)

Add a fine-grained GitHub PAT as repository secret **`RELEASES_REPO_TOKEN`** with **Contents: Read and write** on [matthewfkoch/APITuner-releases](https://github.com/matthewfkoch/APITuner-releases). The release workflow uses it to create public GitHub Releases with the APK attached.

Copy `distribution/README.md` into the releases repo for the landing page (one-time).

### Optional: signed release APK

Add these GitHub repository secrets to produce a signed release APK instead of debug:

| Secret | Description |
| ------ | ----------- |
| `KEYSTORE_BASE64` | Base64-encoded `.jks` keystore |
| `KEYSTORE_PASSWORD` | Keystore password |
| `KEY_ALIAS` | Key alias |
| `KEY_PASSWORD` | Key password |

Generate a keystore locally (keep it private):

```bash
keytool -genkey -v -keystore apituner-release.jks -alias apituner \
  -keyalg RSA -keysize 2048 -validity 10000
base64 -i apituner-release.jks | pbcopy   # paste into KEYSTORE_BASE64
```

## Accepted trade-offs

- No true force-stop on any backend (Android has no non-privileged force-stop). APITuner navigates HOME and can send a `key_macro` to handle prompts.
- **`http_agent` is the reliable choice for deep-link tuning** — it pins the target package. `androidtv_remote` is kept for pairing-only or key-macro workflows where an APK cannot be installed.
- `androidtv_remote` requires the pre-installed Android TV Remote Service (present on Google TV / Android TV, absent on Fire TV).
- Cast-based playback detection from Docker bridge networking can be unreliable; the Agent's foreground/usage detection is more dependable in practice.

## Licensing

- This project is licensed under **Apache License 2.0** — see [LICENSE](LICENSE).
- The `agent/` app is derived from **DisplayLauncher** (Apache-2.0). See `agent/NOTICE` for attribution to [mouldybread/DisplayLauncher](https://github.com/mouldybread/DisplayLauncher).
- `androidtvremote2` and `pychromecast` are used as standard PyPI dependencies under their respective licenses.
