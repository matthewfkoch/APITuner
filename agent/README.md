# APITuner Agent

An Android app that exposes an ADB-free HTTP control API for APITuner's **`http_agent` backend** (the recommended default). Derived from [DisplayLauncher](https://github.com/mouldybread/DisplayLauncher) (Apache-2.0); see `LICENSE` and `NOTICE`.

Use this backend for **Google TV, YouTube TV, and Fire TV**. It package-pins deep links so channels open directly. Use `androidtv_remote` only when the APK cannot be installed.

## Install

- Download `apituner-agent-<version>.apk` from the [GitHub Releases](https://github.com/matthewfkoch/APITuner-releases/releases) page, or grab a debug APK from the **Build APITuner Agent APK** workflow artifacts on `main` between tagged releases.
- Sideload onto the device (ADB, Downloader app, etc.).
- Open the app once and grant permissions:
  - **Display over other apps** → required to launch apps from the background (REQUIRED)
  - **Usage Access** → foreground-app detection (`current_app`)
  - **Notification Access** → media playback state (`playback_state`)
  - **Accessibility** → global `BACK` / `HOME` / `RECENTS` keys (optional)
  - **Default Home app** → optional kiosk / launcher setups only
- The app runs a foreground service on **port 9092** and advertises itself over mDNS (`_apituner._tcp`).
- After the first launch, the service **auto-starts on device reboot** (and after APK updates) — you do not need to open the app again.
- The Agent registers as an optional HOME / launcher candidate (DisplayLauncher heritage). You do not need to set it as the default launcher for normal `http_agent` tuning.

**Google TV / Android TV:** grant permissions in Settings via the Agent’s buttons (no ADB).

**Fire Stick / Fire TV:** Fire OS often hides overlay / usage / notification toggles for sideloaded apps. Use the APITuner dashboard **Grant permissions (ADB)** once (network ADB). Day-to-day tuning stays on this HTTP API — that one-time ADB step is only for setup. See the root [README](../README.md).

## HTTP API (port 9092)

If a token is configured on the device (APITuner Agent app → **Save API token**) or in the dashboard tuner form, send it as the `X-Auth-Token` header.

| Method | Path                 | Body / notes                                                    |
| ------ | -------------------- | --------------------------------------------------------------- |
| GET    | `/api/health`        | `{ success, message }`                                          |
| GET    | `/api/info`          | model, manufacturer, androidVersion, sdkInt, **versionName**, **versionCode**, packages, capabilities |
| GET    | `/api/apps`          | `[ { name, packageName } ]`                                     |
| GET    | `/api/foreground`    | `{ packageName, hasPermission }`                                |
| GET    | `/api/playback`      | `{ playing, package, hasPermission }`                           |
| POST   | `/api/launch`        | `{ packageName }`                                               |
| POST   | `/api/launch-intent` | `{ packageName, action?, data?, component?, extra_string? }`    |
| POST   | `/api/stop`          | go HOME (no non-root force-stop)                                |
| POST   | `/api/key`           | `{ key }` — `BACK` / `HOME` / `RECENTS`                         |
| POST   | `/api/uninstall`     | `{ packageName }` (opens system dialog)                         |
| POST   | `/api/upload-apk`    | multipart `file` (opens installer)                              |

## Updates

The Agent can update itself from [APITuner-releases](https://github.com/matthewfkoch/APITuner-releases/releases):

- **In-app:** open the Agent UI → **Updates** → **Check for updates** (or leave **Auto-check** on; checks about once per day).
- **Dashboard:** on each `http_agent` tuner card, when a newer release exists, click **Update Agent**.

Both paths download the APK and open the system Install dialog — confirm once with the TV remote. Fully silent installs are not available without Device Owner.

Upgrades require the same signing key as the installed build. Published release APKs should use the release keystore secrets; switching from a debug APK to a signed release requires uninstalling first.

## Build locally

Requires JDK 17 and the Android SDK.

```bash
cd agent
./gradlew assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

The Gradle wrapper is included — no separate Gradle install required.
