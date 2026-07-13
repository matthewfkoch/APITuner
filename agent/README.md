# APITuner Agent

An Android app that exposes an ADB-free HTTP control API for APITuner's `http_agent` backend. Derived from [DisplayLauncher](https://github.com/mouldybread/DisplayLauncher) (Apache-2.0); see `LICENSE` and `NOTICE`.

Use this backend for devices without the Android TV Remote Service (e.g. **Fire TV**) or when you want the installed-app list / sideload installer.

## Install

- Download `apituner-agent-debug.apk` from the **Build APITuner Agent APK** GitHub Actions artifact, or build locally (below), and sideload it onto the device.
- Open the app once. Grant the optional permissions to unlock capabilities:
  - **Usage Access** → foreground-app detection (`current_app`)
  - **Notification Access** → media playback state (`playback_state`)
  - **Accessibility** → global `BACK` / `HOME` / `RECENTS` keys
- The app runs a foreground service on **port 9092** and advertises itself over mDNS (`_apituner._tcp`).

All permissions are granted by the user in Settings — no ADB, no root.

## HTTP API (port 9092)

If a token is configured, send it as the `X-Auth-Token` header.

| Method | Path                 | Body / notes                                                    |
| ------ | -------------------- | --------------------------------------------------------------- |
| GET    | `/api/health`        | `{ success, message }`                                          |
| GET    | `/api/info`          | model, manufacturer, androidVersion, sdkInt, packages, capabilities |
| GET    | `/api/apps`          | `[ { name, packageName } ]`                                     |
| GET    | `/api/foreground`    | `{ packageName, hasPermission }`                                |
| GET    | `/api/playback`      | `{ playing, package, hasPermission }`                           |
| POST   | `/api/launch`        | `{ packageName }`                                               |
| POST   | `/api/launch-intent` | `{ packageName, action?, data?, component?, extra_string? }`    |
| POST   | `/api/stop`          | go HOME (no non-root force-stop)                                |
| POST   | `/api/key`           | `{ key }` — `BACK` / `HOME` / `RECENTS`                         |
| POST   | `/api/uninstall`     | `{ packageName }` (opens system dialog)                         |
| POST   | `/api/upload-apk`    | multipart `file` (opens installer)                              |

## Build locally

Requires JDK 17 and the Android SDK.

```bash
cd agent
gradle wrapper --gradle-version 8.9
./gradlew assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

CI builds the debug APK automatically on changes under `agent/`.
