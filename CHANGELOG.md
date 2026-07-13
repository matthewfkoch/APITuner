# Changelog

All notable changes to APITuner are documented here. Version numbers match
`server/apituner/__init__.py` and the Agent APK `versionName`.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- GitHub Release workflow (Docker image + Agent APK)
- Server CI with pytest suite
- Issue/PR templates and Dependabot
- Dashboard channel search, pairing status, API docs link
- Agent on-device auth token setting
- Channel duplicate-number validation on import
- `config.example.json`, `SECURITY.md`, `CONTRIBUTING.md`, root `LICENSE`

### Changed
- **Default backend is now `http_agent`** (recommended for YouTube TV / Google TV)
- Merged Dependabot updates: AGP 9.2.1 (built-in Kotlin), Gradle 9.4.1, Android SDK 37, Python server deps
- Agent `minSdk` raised to 23 (required by androidx.core 1.19.0)
- Agent foreground detection falls back to recent usage stats
- Same-app channel switches accepted after short readiness delay
- `request_timeout` and `keep_apps_running` options are now functional
- Channel export includes `action`, `extra_string`, and `key_macro`

### Fixed
- Tune failures when switching channels inside the same app (e.g. ABC → ESPN)
- Background activity launch blocked without Display-over-other-apps permission

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

[Unreleased]: https://github.com/matthewfkoch/APITuner/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/matthewfkoch/APITuner-releases/releases/tag/v0.1.1
[0.1.0]: https://github.com/matthewfkoch/APITuner-releases/releases/tag/v0.1.0
