# Changelog

All notable changes to APITuner are documented here. Tagged releases keep
`server/apituner/__init__.py` and the Agent APK `versionName` in sync; work under
`[Unreleased]` may briefly advance the Agent ahead of the server.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.1.10] - 2026-07-20

### Fixed
- Fire Stick: Accessibility / Send keys never binds after ADB grant — `KeyAccessibilityService` must be `android:exported="true"` so Fire OS can bind it (still protected by `BIND_ACCESSIBILITY_SERVICE`)

## [0.1.9] - 2026-07-20

### Fixed
- Fire Stick / Android 9: Agent UI crash on launch — logo gradient `angle` must be a multiple of 45 (`bg_logo_mark`)

## [0.1.8] - 2026-07-20

### Added
- Dashboard **Download diagnostics** (`GET /api/diagnostics`): redacted support bundle with recent server logs, tuner status, and live Agent probes (tokens stripped; LAN IPs may appear). Agent adds `GET /api/diagnostics` for permission/capability snapshots
- `ready_settle_seconds` option (default 1s): brief wait after MediaSession PLAYING before opening the HDMI stream

### Fixed
- Fire Stick **Grant permissions (ADB)** no longer `am force-stop`s the Agent afterward (Fire OS clears Accessibility on force-stop, leaving Send keys red). Also calls `cmd notification allow_listener` for a more durable notification grant
- Encoder stream proxy follows HTTP 301/302 redirects (fixes Channels 503 when the encoder redirects, e.g. trailing slash)
- Tune readiness no longer treats “app in foreground” alone as ready while waiting for playback — reduces home/YTTV splash in the stream when Notification Access works

### Changed
- Dashboard setup copy calls out **Grant permissions (ADB)** on each Agent tuner card for Fire TV
- Playback check / wait-for-playback copy clarifies MediaSession-based readiness

## [0.1.7] - 2026-07-17

### Added
- Dashboard **Grant permissions (ADB)** for one-time Fire Stick / Fire TV Agent setup (overlay, usage, notification). Day-to-day tuning remains on the Agent HTTP API
- Docker image includes `adb` for that Fire setup path

### Fixed
- Agent permission buttons on Android 14 Google TV / Chromecast: Settings intents were filtered out by package-visibility `resolveActivity` checks, so taps only showed a Toast. Buttons now open the matching Special app access / Accessibility / Home screens
- ADB grant no longer overwrites other apps’ notification-listener / accessibility-service entries (append/merge instead)
- Dashboard grant toast honors `success` / shows ADB detail; confirm dialog before running
- Fire Agent: “Open settings” after a successful grant no longer re-opens the ADB help dialog
- Agent `isDebugBuild` detection (debug APKs were misclassified as release)
- Agent Default Home badge reflects whether the Agent holds the HOME role

### Changed
- README / agent / distribution / SECURITY docs cover the Fire Stick one-time network-ADB exception and Android 14 rationale

## [0.1.6] - 2026-07-15

### Fixed
- Discover → Add treated the device as an existing tuner (PUT without id) and flashed "Tuner not found"
- ADBTuner import: clearer 400 errors for null channel numbers and duplicate numbers (with channel names), plus quirk normalization (`sort_order`, string numbers, empty alternate package, numeric station IDs)
- Agent crash on Fire OS 7 / Android 9 (API 28): Usage Access check used `unsafeCheckOpNoThrow` (API 29+)

### Changed
- Docker image includes `curl` for in-container Agent reachability diagnostics
- README troubleshooting for Synology/bridge Agent unreachable, import failures, and Fire TV limits
- Agent UI is easier to navigate with a D-pad remote (focus rings, larger full-width buttons, token field no longer steals focus)

## [0.1.5] - 2026-07-13

### Added
- Multi-arch Docker images (`linux/amd64` and `linux/arm64`) for GHCR releases

## [0.1.4] - 2026-07-13

### Added
- HDHomeRun tuner emulation (`/discover.json`, `/lineup.json`, `/auto/v{channel}`, `/tuner{n}/v{channel}`)
- SSDP (UDP 1900) + SiliconDust UDP (65001) discovery for Channels DVR auto-detect
- XMLTV guide at `/xmltv.xml` remapped from Channels DVR (Gracenote StationIDs)
- Dashboard sidebar shows HDHomeRun device URL and tuner count
- Options for HDHomeRun name, DeviceID, optional port, discovery toggles, and XMLTV source settings
- Agent APK self-update: checks `latest.json` from APITuner-releases (in-app button + optional daily auto-check)
- Dashboard **Update Agent** on `http_agent` tuner cards when a newer APK is available
- Release workflow publishes `latest.json` (versionName, versionCode, apkUrl, sha256) next to the APK
- `GET /api/agent/latest` and `POST /api/tuners/{id}/update-agent`

### Changed
- Public-ready docs: HDHomeRun-first distribution/server READMEs, issue forms, CODEOWNERS, Dependabot for GitHub Actions
- Agent `/api/info` now reports `versionName` / `versionCode`

## [0.1.3] - 2026-07-13

### Added
- `/channels.m3u8` playlist endpoint (ADBTuner-compatible URL for Channels DVR)
- `?provider=` query parameter on `/channels.m3u` and `/channels.m3u8` to filter by `provider_name`

## [0.1.2] - 2026-07-13

### Changed
- Merged Dependabot updates: AGP 9.2.1 (built-in Kotlin), Gradle 9.4.1, Android SDK 36, Python server deps
- Agent `minSdk` raised to 23; `core-ktx` 1.18.0 (CI-compatible; API 37 platform not yet in sdkmanager)

## [0.1.1] - 2026-07-13

### Changed
- Dashboard Agent APK links use the public `APITuner-releases` repo (configurable via `APITUNER_AGENT_APK_URL`)
- Default server port is now **6592** (was 5593) to avoid conflict with ADBTuner on the same host

## [0.1.0] - 2026-07-13

### Added
- Initial release: FastAPI server, web dashboard, M3U playlist, stream proxy
- Pluggable backends: `http_agent` and `androidtv_remote`
- APITuner Agent APK (derived from DisplayLauncher, Apache-2.0)
- Docker Compose deployment on port 5593
- ADBTuner-compatible channel import/export
- mDNS discovery for Android TV Remote and Agent services
- Tuner pool orchestration with capability-aware selection

[Unreleased]: https://github.com/matthewfkoch/APITuner/compare/v0.1.7...HEAD
[0.1.7]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.7
[0.1.6]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.6
[0.1.5]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.5
[0.1.4]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.4
[0.1.3]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.3
[0.1.2]: https://github.com/matthewfkoch/APITuner/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/matthewfkoch/APITuner/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.0
