# Changelog

All notable changes to APITuner are documented here. Version numbers match
`server/apituner/__init__.py` and the Agent APK `versionName`.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- HDHomeRun tuner emulation (`/discover.json`, `/lineup.json`, `/auto/v{channel}`, `/tuner{n}/v{channel}`)
- SSDP (UDP 1900) + SiliconDust UDP (65001) discovery for Channels DVR auto-detect
- XMLTV guide at `/xmltv.xml` remapped from Channels DVR (Gracenote StationIDs)
- Dashboard sidebar shows HDHomeRun device URL and tuner count
- Options for HDHomeRun name, DeviceID, optional port, discovery toggles, and XMLTV source settings

### Changed
- Public-ready docs: HDHomeRun-first distribution/server READMEs, issue forms, CODEOWNERS, Dependabot for GitHub Actions

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

[Unreleased]: https://github.com/matthewfkoch/APITuner/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.3
[0.1.2]: https://github.com/matthewfkoch/APITuner/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/matthewfkoch/APITuner/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.0
