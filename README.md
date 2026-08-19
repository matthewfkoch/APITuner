# APITuner

An **ADB-free day-to-day** virtual tuner for [Channels DVR](https://getchannels.com/), in the spirit of ADBTuner. APITuner controls networked Android TV / Google TV devices through pluggable control backends and relays each device's paired HDMI-encoder stream to Channels — as an **HDHomeRun-compatible tuner** (recommended, for Tuner Sharing / multi-TV sync) or as a Custom Channels (M3U) source.

- **Day-to-day tuning uses the Agent HTTP API — not ADB.** That is the point of APITuner vs ADBTuner: **Android 14 broke reliable wired ADB** on modern Google TV / Android TV devices, so APITuner moved control off ADB entirely for normal operation.
- **Default backend:** the bundled **APITuner Agent** APK (derived from [DisplayLauncher](https://github.com/mouldybread/DisplayLauncher)) — package-pinned deep links that work reliably with YouTube TV and other streaming apps.
- **Alternate backend:** the Android TV Remote protocol v2 via [`androidtvremote2`](https://github.com/tronikos/androidtvremote2), with optional [`pychromecast`](https://github.com/home-assistant-libs/pychromecast) for playback detection. Simpler setup (pair once, no APK) but **cannot pin the target app** on deep links, which often triggers Android's "Open with" chooser.
- Runs in Docker. Web dashboard on port **6592**.

> **Fire Stick / Fire TV exception (setup only):** Fire OS does not expose a Permissions page for sideloaded apps, so overlay / usage / notification access cannot be toggled in Settings. APITuner can grant those **once** over **network ADB** from the dashboard (**Grant permissions (ADB)**). Fire Sticks run older Android builds and are **not** impacted by the Android 14 ADB regressions that broke ADBTuner on modern TVs. After that one-time grant, tuning still goes through the Agent HTTP API — ADB is not used per tune.

> APITuner still requires an **HDMI encoder** per device (like ADBTuner). Streaming apps are DRM-protected, so a device cannot screen-capture itself; the encoder captures the device's HDMI output and serves it as MPEG-TS.

---

## How it works

```
Channels DVR ──HDHomeRun /auto/v…──▶ APITuner ──control──▶ Android TV device
                (or /channels.m3u)       │                     (launches app / deep link)
                                         └──relay MPEG-TS◀── HDMI encoder ◀─HDMI─ device
```

1. Channels DVR requests a channel (HDHomeRun lineup or M3U).
2. APITuner picks a free, eligible tuner and tells its device to launch the channel's app / deep link.
3. It waits until the app is playing (playback state, foreground app, or a fixed delay depending on the backend), optionally sends a key macro to clear prompts.
4. It relays the paired HDMI encoder's MPEG-TS to Channels and releases the tuner when the stream ends.

## Control backends

| Capability       | `http_agent` (Agent APK) **recommended for deep links** | `androidtv_remote` (Google TV Remote) | `firetv_rest` (Fire TV Remote HTTP) | `adb` (network ADB) |
| ---------------- | -------------------------------------------------------- | ------------------------------------- | ----------------------------------- | ------------------- |
| Launch/deeplink  | ✅ package-pinned (reliable)                              | ⚠️ bare URL only (app chooser risk)   | ✅ package launch (no deep link)    | ✅ package + deep link |
| Key events       | ⚠️ BACK/HOME/RECENTS                                     | ✅ full D-pad                         | ✅ full D-pad                       | ✅ full D-pad (+ force-stop) |
| App Play configs | ❌                                                       | ✅                                    | ✅ (newer Fire with `:8080`)        | ✅ Fire fallback |
| Foreground app   | ✅ (Usage Access)                                         | ✅                                    | ❌                                  | ✅ (dumpsys) |
| Playback state   | ✅ (Notification Access)                                  | ✅ (via Cast, LAN-dependent)          | ❌ (use fixed delay)                | ❌ (use fixed delay) |
| App list/install | ✅                                                        | ❌                                    | ❌                                  | ✅ list / ❌ install |
| Setup            | Install APK + 2 permissions                              | Pair once, no APK                     | Pair once (PIN), no APK             | Enable network ADB |
| Best for         | **YouTube TV, Google TV, Fire TV deep links**            | App Play on Google TV                 | App Play on newer Fire              | App Play on older Fire OS 7 |

**Use `http_agent` for production deep-link tuning** (especially YouTube TV). The Agent sends intents with an explicit package, like ADBTuner's `am start`, so channels open directly instead of stalling on an "Open with" dialog.

Use `androidtv_remote`, `firetv_rest`, or `adb` when running babsonnexus **App Play** configurations (D-pad navigation), **or** as a hybrid **`keys_control`** plane alongside `http_agent` (recommended for Max profile prompts + YTTV deep links on the same device). Prefer ADB-free backends when they work; use **`adb` on Fire** when `:8080` Fire TV REST is missing.

### Hybrid control (`keys_control`)

Keep **`control: http_agent`** for package-pinned launches and playback probes. Add optional **`keys_control`** on the same tuner for D-pad:

| Device | Primary `control` | `keys_control` |
| --- | --- | --- |
| Google TV / onn / Chromecast | `http_agent` `:9092` | `androidtv_remote` `:6466` / pair `:6467` |
| Fire Stick | `http_agent` `:9092` | `firetv_rest` `:8080` or `adb` `:5555` |

Pair the keys backend from the dashboard (**Pair** / **Auto-pair**). Auto-pair OCRs the PIN from the tuner’s HDMI encoder feed (needs `stream_endpoint` plus ffmpeg/tesseract — both are in the Docker image); manual PIN entry remains available. Without `keys_control`, Max `key_macro` / who’s-watching / App Play cannot send `DPAD_CENTER` (Agent Accessibility only supports BACK/HOME/RECENTS).

### Encoder preview (dashboard)

Each tuner card has **Preview**: a live view of that tuner’s HDMI encoder stream (MJPEG via ffmpeg, with a still-frame fallback) plus on-screen remote buttons (D-pad, Enter, Back, Home, volume, wake/sleep; reboot when the keys plane is `adb`).

- Needs a working `stream_endpoint` and **ffmpeg** in the server environment (included in the Docker image).
- Arrows / Enter need **Keys / D-pad** configured and paired; with Agent alone, only **Back** and **Home** work.

### Agent setup (per device)

1. Install the APK from [GitHub Releases](https://github.com/matthewfkoch/APITuner-releases/releases) (or build from `agent/`).
2. Open the app and grant permissions:

#### Google TV / Android TV 10+

Use the Agent’s **Open settings** buttons (or system Settings):

- **Display over other apps** — **required** (allows background app launches)
- **Usage Access** — **recommended** (foreground-app tune readiness)
- **Notification Access** — optional (playback-state detection)
- **Accessibility** — optional (BACK/HOME/RECENTS keys)

No ADB is required.

#### Fire Stick / Fire TV (one-time network ADB)

Fire OS typically **does not show a Permissions page** for sideloaded apps, so the overlay / usage / notification toggles cannot be opened from the Agent. That is separate from day-to-day control:

| | ADBTuner (why people left) | APITuner |
| --- | --- | --- |
| Modern Google TV (Android 14+) | Wired ADB often broken / unreliable | **No ADB for tuning** — Agent HTTP API |
| Fire Stick (older Fire OS) | ADB still usually works | **One-time network ADB** only to grant Agent permissions; **tuning is still Agent HTTP** |

**Setup:**

1. On the Fire TV: **Settings → My Fire TV → Developer Options** → enable **ADB debugging** (and Apps from Unknown Sources if needed).
2. From a computer on the LAN, accept the **Allow USB debugging?** prompt the first time APITuner connects (`adb connect DEVICE_IP:5555`).
3. In the APITuner dashboard, open the Fire Stick tuner card → **Grant permissions (ADB)**. That grants overlay, usage, notification, and Accessibility (Send keys). There is **no on-device Settings path** for these on Fire OS for sideloaded apps — ADB is required. Grant may reboot once so Accessibility binds.
4. After grants succeed, you can leave ADB debugging on or turn it off — **Channels tunes do not use ADB**.

The Docker image includes `adb` so the grant button works from the container when the device is reachable on the LAN. Local `docker-compose.yml` mounts `~/.android` so host “Always allow” ADB keys are reused; otherwise accept the RSA prompt that appears for the container’s key.

3. In the APITuner dashboard, add a tuner with backend `http_agent`, device IP, port `9092`, and the HDMI encoder stream URL (do this before or after the Fire grant step above).

---

## Quick start (Docker)

### From GitHub Container Registry (releases)

```bash
mkdir -p apituner-data
docker run -d \
  --name apituner \
  -p 6592:6592 \
  -v "$(pwd)/apituner-data:/data" \
  -v "$HOME/.android:/root/.android" \
  --restart unless-stopped \
  ghcr.io/matthewfkoch/apituner:latest
```

The `~/.android` mount is optional but recommended if you use dashboard **Grant permissions (ADB)** for Fire Stick setup (shares host ADB keys with the container).

### Build locally

```bash
git clone https://github.com/matthewfkoch/APITuner.git
cd APITuner
docker compose up -d --build
```

Open the dashboard at `http://<docker-host>:6592`.

To use mDNS **Discover** and HDHomeRun auto-discovery (SSDP / UDP 65001), enable host networking (see the comment in `docker-compose.yml`). You can always add tuners by IP and add APITuner as an HDHomeRun source by URL (`http://<host>:6592`) without host networking.

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
2. **Recommended for deep links (YouTube TV / HBO) — `http_agent`:** install the Agent APK (see above), grant permissions, enter the device IP (port `9092`).
3. **App Play / Max prompts — D-pad backend or hybrid `keys_control`:**
   - Prefer **Agent + keys**: primary `http_agent`, then set Keys / D-pad backend to `androidtv_remote` (Google TV) or `firetv_rest` / `adb` (Fire). Pair the keys plane from the dashboard.
   - Or use a single **`androidtv_remote`**, **`firetv_rest`**, or **`adb`** tuner for App Play-only devices.
   - **`adb`** (Fire App Play **fallback**): network ADB on port `5555` when `firetv_rest` is unavailable (common on older Fire OS 7 sticks).
4. Enter the **encoder stream URL** — the HDMI encoder's MPEG-TS endpoint, e.g. `http://192.0.2.20/4.ts`.
See `config.example.json` for a sample configuration.

## Configure channels

Add channels manually or **Import** an ADBTuner channel-list JSON (the schema is compatible). Each channel has:

- `number` — must be unique across the list (fix duplicate numbers in the export before import)
- `package_name` (+ optional `alternate_package_name`)
- `url` — deep link (intent data), e.g. `https://tv.youtube.com/watch/...`, **or** an App Play loop index (`"0"`, `"1"`, …)
- `configuration_uuid` — optional; set for [babsonnexus HDMI Encoder Native Apps](https://github.com/babsonnexus/hdmi-encoder-native-apps) App Play stations
- `action` (default `android.intent.action.VIEW`)
- `component` — explicit activity (used by the Agent backend; Android 12+)
- `key_macro` — keys sent after launch to dismiss prompts (comma or semicolon; needs D-pad via primary remote/adb **or** hybrid `keys_control`)
- `compatibility_mode`, `tvc_guide_stationid`

Dynamic / lane URLs (FruitDeepLinks, OliveTin, ADBTuner-style resolvers) are fetched at tune time when the URL looks like a resolver (`/lanes/`, `/whatson/`, `dynamic_url_json_key=…`, or `format=json|text` on a deeplink API). The stored URL stays the resolver; only the resolved deeplink is launched.

**FruitDeepLinks (Android / Google TV sticks):** set the FDL base URL in Options and click **Sync FruitDeepLinks**, or Import an ADB M3U / playlist URL. Packages are filled from the [deeplink catalog](docs/INTEGRATION.md). Use FDL `/m3u/adb` (not Apple `profile=apple`) and the Agent (`http_agent`) backend. See [docs/INTEGRATION.md](docs/INTEGRATION.md).

ADBTuner exports sometimes have `"number": null`. APITuner fills that from `sort_order` when present; otherwise import returns a clear 400 error naming the channel instead of an internal server error.

### App Play configurations (babsonnexus)

Native apps without reliable deep links (ESPN, CBS, Fox, NBC, …) use ADBTuner **configurations**: D-pad scripts with `adbtuner_open_app`, `input keyevent`, `sleep`, and `ADB_LOOP`. APITuner runs those on backends that expose full D-pad (including hybrid `keys_control`):

1. In the dashboard **Configurations** tab, import JSON from `adbtuner_native/configurations/*.json`.
2. On **Channels**, import the matching station list (keeps `configuration_uuid`).
3. Use a D-pad backend **or** Agent + `keys_control` on the tuner (`http_agent` alone cannot inject D-pad). On older Fire sticks without `:8080`, use **`adb`** as `keys_control` or primary.
4. **ESPN package:** babsonnexus stations use `com.espn.score_center`. Some Google TV devices use `com.espn.gtv` instead — set `package_name` / `alternate_package_name` to match what’s installed. APITuner tries primary → alternate → ESPN family until one opens, and **Check packages** on the Channels page flags packages missing from Agent devices. Wrong package on Google TV often opens the **Play Store**.
5. Long App Play scripts can exceed Channels’ ~30s connect timeout; **Stream during App Play** (Options, on by default) keeps the encoder stream open while D-pad runs so Channels does not mark the tuner unreachable.
6. On mixed Fire + Chromecast fleets, **App Play stick preference** (Options) chooses which D-pad path is tried first when layouts differ (e.g. Fire-tuned ESPN).

Deep-link stations with a `configuration_uuid` (e.g. Max + ADBTuner Compatibility Mode) use the normal Agent launch path **and** apply that config as an overlay: `pre_tune_commands`, who’s-watching clears when `check_for_and_clear_whos_watching_prompts` is true, `key_macro`, and `post_playback_start_commands`. Redundant `am start` lines are skipped when the Agent already launched the deeplink.

**Who’s-watching:** when the config flag is on, APITuner briefly samples the HDMI encoder with ffmpeg + tesseract OCR (≈3.5s budget, fast exit if no prompt). Needs `keys_control` / D-pad for Select — without it the tune **fails** (`skipped_no_dpad`) instead of opening the profile screen as ready. YTTV and configs with the flag off pay zero OCR cost. `am force-stop` is real on the `adb` keys plane; on Remote/FireTV REST it maps to best-effort HOME/stop. Agent launches that return HTTP 4xx or `success: false` also fail the tune.

Fire TV REST notes: reverse-engineered Amazon protocol; TLS verification is disabled; may need a wake on port `8009`; can break on Fire OS updates. If `:8080` never opens after wake (common on Fire OS 7 / older sticks), switch the tuner to **`adb`**.

## Connect to Channels DVR

### Recommended: HDHomeRun tuner (multi-TV sync)

APITuner can appear as an **HDHomeRun** network tuner. Channels DVR then treats it like a native SiliconDust device, which enables **Tuner Sharing** — one physical tune fans out to multiple TVs watching the same channel (much tighter sync than Custom Channels).

1. In Channels DVR: **Settings → Add Source → HDHomeRun**.
2. Either wait for auto-discovery, or enter the URL shown in the dashboard sidebar (e.g. `http://<host>:6592`).
3. Scan channels; assign Gracenote / guide data as you would for any HDHR source.
4. **Enable Tuner Sharing** on clients: **Settings → Playback → Advanced → Tuner Sharing** (or force it via DVR server-side client settings).
5. Remove or disable the Custom Channels M3U source if you previously used it — otherwise Channels may use both and waste tuners.

#### Guide data (Gracenote via Custom URL)

HDHomeRun sources don't read `tvc-guide-stationid` the way M3U Custom Channels do. APITuner can instead serve an XMLTV feed remapped from your Channels DVR guide (matched by Gracenote StationID):

1. In APITuner **Options**, set **Channels DVR URL** to your DVR (e.g. `http://192.0.2.30:8089`) and **XMLTV source device** (default `M3U-YouTubeTV`).
2. In Channels, on the APITuner HDHomeRun source, set guide provider to **Custom URL**.
3. Paste `http://<apituner-host>:6592/xmltv.xml` and Save.

`TunerCount` equals the number of **enabled** tuners in APITuner (one device + HDMI encoder per slot).

> Auto-discovery (SSDP + SiliconDust UDP port 65001) needs multicast. In Docker, use `network_mode: host` (see `docker-compose.yml`). Manual IP entry works on bridge networking.

### Alternate: Custom Channels (M3U)

In Channels DVR: **Settings → Add Source → Custom Channels → M3U URL** and paste the URL shown at the top of the dashboard:

```
http://<docker-host>:6592/channels.m3u
```

Custom Channels does **not** support Tuner Sharing — each TV typically opens its own stream, which is why the same channel can look out of sync across rooms.
## Global options

Configurable in the dashboard:

| Option | Description |
| ------ | ----------- |
| Tune timeout | Max seconds to wait for a channel to become ready |
| Wait for playback | Prefer a playing MediaSession before accepting a tune (falls back to foreground if playback never appears) |
| Ready settle | Extra seconds after playback is detected before opening the HDMI stream |
| Stop on release | Send HOME when the stream ends |
| Keep apps running | When off, always send HOME on release (overrides keep-warm behavior) |
| Retry on other tuner | Try another eligible tuner if a tune fails |
| Request timeout | HTTP timeout for Agent API calls (seconds) |
| Stream mode | `proxy` (default, like ADBTuner) or `redirect` (Channels hits encoder directly; M3U only) |
| Release grace | Seconds to hold tuner lock after stream disconnect |
| Stuck / idle timeouts | Reclaim tuners that stop making progress |
| HDHomeRun emulation | Appear as an HDHomeRun tuner (`discover.json` / `lineup.json` / `/auto/v…`) |
| HDHomeRun discovery | SSDP + UDP 65001 (optional; needs host networking in Docker) |
| XMLTV cache | How long to reuse a built `/xmltv.xml` |
| FruitDeepLinks URL | LAN base URL of [FruitDeepLinks](https://github.com/kineticman/FruitDeepLinks) (`http://host:6655`). Sync ADB lanes; remap FDL XMLTV onto those channel numbers |
| FruitDeepLinks profile | `google_tv` or `fire` — primary Android package (ESPN split); the other is stored as alternate |
| FruitDeepLinks start number | First number for synced lanes (default 9000) |
| FruitDeepLinks XMLTV path | Path on the FDL server (default `/xmltv/adb`) |
| FruitDeepLinks auto-sync | Seconds between background lane refreshes; `0` = Sync button only |

- `proxy` (default) — APITuner relays the encoder stream and releases the tuner on disconnect.
- `redirect` — Channels connects to the encoder directly (lower server load; tuner reclaimed after idle timeout). **Not used for HDHomeRun streams** (those always proxy so lock lifecycle stays correct).

HDHomeRun endpoints (`/discover.json`, `/lineup.json`, `/auto/v{channel}`, `/tuner{n}/v{channel}`) are enabled by default. Disable with **HDHomeRun emulation** in Options if you only want the M3U source.

---

## Repository layout

- `server/` — the APITuner FastAPI service (including `hdhr/` HDHomeRun + XMLTV). See [server/README.md](server/README.md).
- `agent/` — the APITuner Agent Android app (`http_agent` backend). CI builds the APK (`.github/workflows/agent-build.yml`).
- `distribution/` — landing-page README for the public APK releases repo.
- `config.example.json` — sample tuners, channels, and options.
- `docs/INTEGRATION.md` — FruitDeepLinks / Android TV deeplink integrator contract.
- `CHANGELOG.md` — version history.

## Troubleshooting

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| Tune times out, TV shows "Open with" | `androidtv_remote` backend | Switch to `http_agent` and install the Agent APK |
| Agent launch succeeds but app doesn't open | Missing "Display over other apps" | Grant overlay permission on the device |
| Same-app channel switch times out | Usage Access not granted | Grant Usage Access; use latest server |
| Discover finds nothing in Docker | Bridge network blocks mDNS | Use `network_mode: host` or add tuners manually by IP |
| Tune succeeds but Channels shows 503 / no video | Encoder URL returns HTTP 301/302 and older builds did not follow redirects | Update server; confirm encoder URL in a browser/`curl -L`. Proxy mode now follows redirects |
| Need logs for forum support | — | Options → **Download diagnostics** (redacted JSON: recent logs, tuner probes; tokens stripped) |
| Agent reachable from host, **Unreachable** from dashboard | Container cannot reach device LAN (common on Synology / some NAS bridges) | From the host: `curl http://DEVICE_IP:9092/api/health`. From the container: `docker exec apituner curl -v --connect-timeout 5 http://DEVICE_IP:9092/api/health`. If host works but container fails, try host networking, check the NAS firewall for **outbound** TCP 9092, or use a macvlan/host network so the container shares the LAN |
| Discover shows device but Add fails / "Tuner not found" | Older UI bug treating Discover as an edit | Update to a build that posts new tuners from Discover; fill in the encoder stream URL before saving |
| Import fails / Internal Server Error | Null `number` or duplicate channel numbers in ADBTuner JSON | Fix numbers in the export (or rely on `sort_order`); current builds return a named 400 error instead of 500 |
| FruitDeepLinks lanes missing packages / skipped | Provider not in the deeplink catalog | Check `GET /api/deeplink-catalog`; import M3U with `package-name=` or file an APITuner mapping |
| Fire TV Agent has no Permissions page / tune times out on Fire | Fire OS hides overlay/usage/notification toggles for sideloaded apps | One-time: enable Fire **ADB debugging**, then dashboard → tuner → **Grant permissions (ADB)**. Day-to-day tuning stays on the Agent (no ADB). Fire Sticks are not affected by Android 14’s wired-ADB breakage |
| Grant permissions (ADB) → unauthorized / unreachable | Container has different ADB keys than the host, or TCP **5555** blocked | Mount `$HOME/.android` into the container (see `docker-compose.yml` / `docker run` above); accept **Allow USB debugging** on the TV; ensure the container can reach `DEVICE_IP:5555` |
| Grant reports success but Agent badges stay red | Agent still restarting, or capability refresh raced | Wait a few seconds → **Recheck connection**; confirm overlay/usage with the Agent UI |
| Grant succeeds but Accessibility / Send keys goes red again | Older builds `am force-stop`’d the Agent after grant; Fire OS clears `enabled_accessibility_services` on force-stop; or Accessibility listed in settings but not bound until reboot | Update APITuner and re-run **Grant permissions (ADB)** (grant may reboot once to bind). There is no on-device Settings grant path on Fire for sideloaded apps |
| Accessibility / keys lost after APK reinstall | Fire OS may clear the accessibility binding | Re-run **Grant permissions (ADB)** (no on-device Settings toggle) |
| Preview arrows / Enter do nothing or toast “needs a D-pad backend” | Tuner is Agent-only (`keys_control` unset / unpaired) | Edit tuner → set **Keys / D-pad** to `androidtv_remote` / `firetv_rest` / `adb`, Pair, retry. Back/Home work without that |
| Agent crashes on open (Fire OS 7 / Android 9) | Older Agent used an API 29 AppOps call | Update to Agent **0.1.6+**. After reinstall, re-grant permissions if needed |
| HDHomeRun not auto-detected | SSDP/UDP 65001 blocked on Docker bridge | Host networking, or add source URL `http://<host>:6592` manually |
| HDHomeRun guide empty | XMLTV not configured | Set Channels DVR URL + XMLTV source device; use Custom URL `…/xmltv.xml` |
| `androidtv_remote` playback never ready | Cast/mDNS unreachable from Docker | Use Agent backend, or host networking |
| No free tuner | All tuners locked | Wait for stream to end, or lower idle/stuck timeouts |

**Note on host networking:** needed for mDNS Discover and HDHomeRun auto-discovery. Bridge mode is fine for day-to-day tuning if you add tuners **by IP** and add Channels sources by URL. Prefer bridge when you do not need multicast discovery.

## Security

The dashboard and API on port **6592** are **not authenticated**. Do not expose APITuner to the public internet. See [SECURITY.md](SECURITY.md).

## Releases

Tagged releases (`v*`) trigger `.github/workflows/release.yml`, which:

1. **Publishes the server** to GitHub Container Registry: `ghcr.io/matthewfkoch/apituner:<version>` (multi-arch: `linux/amd64` + `linux/arm64`; package visibility should be **Public**)
2. **Builds the Agent APK** and attaches it to [APITuner-releases](https://github.com/matthewfkoch/APITuner-releases/releases) for public download

To cut a release:

```bash
git tag v0.1.7
git push origin v0.1.7
```

Bump `server/apituner/__init__.py` and the Agent `versionName`/`versionCode` first, move `[Unreleased]` notes in `CHANGELOG.md` into the new version section, then tag. Between releases, debug APK artifacts are available from the **Build APITuner Agent APK** workflow on `main`.

### Agent APK releases repo

APKs are published to the companion [APITuner-releases](https://github.com/matthewfkoch/APITuner-releases) repo so download URLs stay stable for users who only need the APK.

Add a fine-grained GitHub PAT as repository secret **`RELEASES_REPO_TOKEN`** with **Contents: Read and write** on that repo. The release workflow uses it to create GitHub Releases with the APK and a machine-readable **`latest.json`** attached (also at `…/releases/latest/download/latest.json`). Fine-grained PATs expire — if a tagged Release fails with `Bad credentials` on **Publish public release**, regenerate the PAT and update the secret, then re-run failed jobs. A GitHub App installation token on `APITuner-releases` avoids the 90-day expiry.

Copy `distribution/README.md` into the releases repo for the landing page (one-time).

### Agent APK updates

The Agent can update from Releases without re-sideloading manually:

1. **In the Agent app** — **Check for updates** (or leave auto-check enabled; about once per day).
2. **In the dashboard** — on an `http_agent` tuner, when “Update available” shows, click **Update Agent**.

Both open the system Install dialog on the TV; confirm once with the remote. **Network ADB** (`adb install -r`) can apply the same APK without that dialog if the stick already trusts this host. Override the manifest URL with `APITUNER_AGENT_LATEST_URL` if needed.

**Signing:** upgrades only work when the new APK is signed with the same key as the installed one. Configure the release keystore secrets below for durable upgrade paths; a debug→release switch requires uninstalling first.

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
- Dashboard **Update Agent** opens the system Install dialog on the TV (Android does not allow a silent update over HTTP without Device Owner). Operators with network ADB already authorized can `adb install -r` instead.

## Licensing

- This project is licensed under **Apache License 2.0** — see [LICENSE](LICENSE).
- The `agent/` app is derived from **DisplayLauncher** (Apache-2.0). See `agent/NOTICE` for attribution to [mouldybread/DisplayLauncher](https://github.com/mouldybread/DisplayLauncher).
- `androidtvremote2` and `pychromecast` are used as standard PyPI dependencies under their respective licenses.
