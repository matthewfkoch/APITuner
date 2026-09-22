# Changelog

All notable changes to APITuner are documented here. Tagged releases bump
`server/apituner/__init__.py`. Agent `versionName` / `versionCode` are bumped
only when the APK changes; `[Unreleased]` may briefly list Agent work ahead of
the server.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [0.1.28] - 2026-09-21

### Changed
- DirecTV splash that never reports MediaSession: HOME off the stuck app before the first deeplink (when the app is already open but idle) and before each relaunch, then send `CLEAR_TASK`. After two idle relaunches, keep waiting **45s** instead of failing at 30s and killing the HDMI proxy (`stream_during_tune`). If a show finally starts after those retries (and there was no leftover PLAYING session), accept it even when the title is not the call sign. Leftover PLAYING (YES while tuning NBC) still fails at the normal timeout. Server only — Agent **0.1.27** is unchanged.

## [0.1.27] - 2026-09-19

### Changed
- DirecTV / similar apps: re-send the channel deeplink **up to twice** if playback stays idle or on a leftover show (continue-watching after a dropped first intent). Cold start (app not already open) retries after 2s instead of 6s.
- Agent deeplink relaunch / cold start can use `FLAG_ACTIVITY_CLEAR_TASK` so DirecTV does not stay on continue-watching. Needs **Agent 0.1.27**. Older Agents still get the second relaunch.
- DirecTV splash title `Unknown Title` is not treated as ready; a later real show title counts as a change (so a 2s retry does not interrupt a cold start that is about to work).
- Agent APK **0.1.27** (`versionCode` 27). Install on each Streamer (dashboard **Update Agent**).

## [0.1.26] - 2026-09-18

### Fixed
- DirecTV same-app tunes treat a **changed** MediaSession program title as ready (Chicago Fire leftover → Golden Girls on MeTV). 0.1.25 required the title to overlap the channel call sign, so correct playback timed out and `stream_during_tune` dropped the HDMI proxy. Unchanged leftover titles (YES while tuning NBC) are still rejected. Server only — Agent **0.1.25** is unchanged.

## [0.1.25] - 2026-09-17

### Added
- Agent playback snapshot includes MediaSession **title** when the app exposes it.

### Changed
- Agent deeplink launches use `FLAG_ACTIVITY_CLEAR_TOP | SINGLE_TOP` so a running app receives the new VIEW URI instead of only coming to the foreground (DirecTV home / continue-watching).
- DirecTV same-app tunes ignore leftover PLAYING until playback drops or a matching title appears; a YES/continue-watching session is not treated as NBC. Wrong titles trigger a deeplink relaunch. Needs Agent **0.1.25** and Notification access.
- Agent APK **0.1.25** (`versionCode` 25). Install on each Streamer (dashboard **Update Agent**).

## [0.1.24] - 2026-09-14

### Added
- **Deeplink relaunch**: if MediaSession playback never starts after a deeplink launch, re-send the same channel intent once after `deeplink_relaunch_seconds` (default 6; `0` = off). Recovers DirecTV (and similar) first tunes after a Google Stream / Android TV reboot when the app opens but the channel deeplink is dropped. Needs Notification access for playback detection. Options → **Deeplink relaunch (s)**.

### Changed
- **Stream during tune** (formerly App Play only) also starts the encoder while deeplink tunes wait for playback, so longer waits / one relaunch stay under Channels' ~30s connect window.
- After a deeplink relaunch that never reaches PLAYING, the tune fails instead of accepting the splash screen as ready.

## [0.1.23] - 2026-09-07

### Changed
- Dynamic / lane URL fetches (`/whatson`, FruitDeepLinks) retry on timeout or connection error (default 15s × 3, 5s connect cap) so a stalled first request does not burn two more tuners. **Lane URL timeout** and **attempts** are in Options. Timeout errors now say `timed out after 15s` instead of an empty message.
- Channels **Copy M3U** is a header menu (All + each provider) instead of chips under the search field.

### Fixed
- Copy M3U dropdown stacks above the channel table so menu items are clickable.
- Copy M3U copies the provider URL to the clipboard only; the sidebar stays on the full playlist unless clipboard write fails.

## [0.1.22] - 2026-09-07

### Added
- M3U `tvg-id` for Channels DVR XMLTV matching. JSON import stores `tvg_id` / `tvg-id`. YTTV Sports rows (`source` `yttv-sports` with `/whatson/{n}`) infer `yttv-sports-{n}` if the field is empty. FruitDeepLinks M3U `tvg-id` is not copied (our XMLTV remaps those lanes onto channel ids). `tvc-guide-stationid` remains Gracenote-only.

### Changed
- Dashboard copy is shorter; internal field names and setup essays are out of the UI.
- Dashboard M3U link shows `/channels.m3u8` and documents `?provider=` filtering in the sidebar.
- Channels page shows **M3U by provider** buttons (when 2+ providers exist) to copy a filtered playlist URL.
- Sidebar URL fields select-all on click for manual copy; import/sync toasts name skipped rows.
- Channel export downloads revoke blob URLs; failed startup status load shows a toast.
- Tuner `PUT` uses the saved row from the URL path (same pattern as channels).

### Fixed
- Editing a channel no longer clears `source` (FruitDeepLinks sync grouping and YTTV Sports `tvg-id` inference).
- Dashboard **Copy** on M3U / HDHomeRun / XMLTV URLs works on plain HTTP LAN (`http://192.168.x.x`) with a fallback when the Clipboard API is blocked.
- `PUT /api/channels/{id}` rolls back in-memory config when validation fails instead of leaving a bad row loaded.
- `PUT /api/channels/{id}` accepts bodies without `id` (id is always taken from the URL path).
- Sidebar M3U URL comes from `/api/status` (same host the browser used to reach APITuner) instead of a one-time `location.origin` snapshot.

## [0.1.21] - 2026-09-01

### Added
- **ATSC subchannel numbers** (`100.1`, `100.2`, …): channel `number` is stored as a string; import, M3U `tvg-chno`, HDHomeRun `GuideNumber`, and tune URLs accept dotted guide numbers. ADBTuner `sort_order` values like `3.0` still normalize to `3`.
- **Duplicate guide numbers** (ADBTuner / OliveTin DirecTV parity): multiple channels may share the same `number` (e.g. MLB Network + MLB Network Alternate at 213). Each row gets a stable internal `id`; M3U uses unique `channel-id` and id-based stream URLs while `tvg-chno` / `channel-number` stay on the dial number.
- `GET /api/export?native=1` includes `id` for APITuner backup/restore round-trip (default export stays ADBTuner-compatible without `id`).

### Changed
- Channel CRUD API is keyed by `id` (`PUT` / `DELETE /api/channels/{id}`).
- Channel `number` is stored as a string internally; `GET /api/export` still emits JSON numbers (`int` / `float`) for ADBTuner compatibility.
- M3U stream URLs are `/stream/{id}`; HDHomeRun lineup URLs are `/auto/v{id}`. Legacy `/stream/{number}` still works when the number is unique.
- XMLTV channel identity uses `id`; Gracenote remap maps station IDs to channel `id`.
- Dashboard **Export** includes channel `id` values (`native` export) for APITuner backup/restore.

### Fixed
- Import no longer rejects duplicate channel numbers in ADBTuner / OliveTin DirecTV exports.

## [0.1.20] - 2026-08-18

### Fixed
- Deeplink tunes try the **channel package first**, then catalog packages for the **stick** (Amazon / `AFT*` / Fire REST → Fire apps). Mixed fleets no longer launch Google YouTube TV on Fire Sticks before the Amazon app.
- YouTube TV catalog Fire package is `com.amazon.firetv.youtube.tv` (was duplicated Google `tvunplugged`).
- Dashboard XMLTV URL is always shown when HDHomeRun is on; “XMLTV ready” when Channels DVR **or** FruitDeepLinks is configured.

### Changed
- `docker-compose.yml` pulls `ghcr.io/matthewfkoch/apituner:latest` by default (`--build` still builds locally).

## [0.1.19] - 2026-08-18

### Added
- **FruitDeepLinks / Android TV deeplink integration**: provider→package catalog (`GET /api/deeplink-catalog`), M3U or playlist-URL import, live ADB-lane or v2 `/whatson` virtual-lane sync, and FDL XMLTV remapped onto APITuner channel numbers. See [docs/INTEGRATION.md](docs/INTEGRATION.md).

## [0.1.18] - 2026-08-08

### Fixed
- Auto-pair no longer restarts pairing after the Pair modal already showed the PIN (androidtv_remote was rebuilding the remote and finishing against a different session)
- Android TV Remote pairing now disconnects the prior SSL session before starting a new one, and Auto-pair recovers when the PIN on screen is from a dead/orphaned pairing dialog
- Auto-pair OCR for Google TV PINs: stop hex-whitelisting the full dialog (it turned “Device pairing…” into garbage), decode common spaced-glyph misreads (e.g. `AQYOATE` → `A90A1E`), and try ranked PIN candidates

## [0.1.17] - 2026-08-08

### Fixed
- Long App Play tunes (e.g. ESPN) no longer trip Channels’ ~30s “Tuner Unreachable” timeout — the encoder stream starts while D-pad navigation runs
- App Play no longer picks Agent-only tuners (no D-pad) or the wrong stick on mixed Fire + Chromecast fleets; Options can prefer Fire or Google TV first
- App Play wakes the device and returns Home first so Google TV screensaver/ambient does not steal navigation into Settings

## [0.1.16] - 2026-08-07

### Added
- **Auto-pair** Keys backends (`androidtv_remote` / `firetv_rest`): `POST /api/tuners/{id}/pair/auto` starts pairing, OCRs the PIN from the HDMI encoder feed, and finishes pairing; dashboard **Auto-pair** button with manual PIN fallback
- **Grant permissions (ADB)** also appears on **Network ADB** Fire tuner cards (not only `http_agent`), so Agent overlay/usage/notification/Accessibility can be granted when the stick is controlled via ADB
- Tuner form **auto-fills the default port** when switching backend type (e.g. `adb` 5555 ↔ `http_agent` 9092) or Keys type, unless the port was customized; `androidtv_remote` also fills **pair port 6467**
- Agent `/api/launch` returns specific failure reasons (`package not installed`, `no LAUNCHER/LEANBACK_LAUNCHER activity`, start errors) instead of opaque `failed`

### Fixed
- Agent `/api/launch` and ADB `open_app` use **LEANBACK_LAUNCHER** (TV-first, then phone `LAUNCHER`) so Leanback-only apps like ESPN open when installed
- ADB `open_app` now **fails** when monkey reports no activities for both categories (was treating abort as success and continuing the App Play script on the wrong screen); non-zero monkey exit on one category still tries the other
- Agent `<queries>` includes Leanback/LAUNCHER MAIN so TV package resolve works on Android 11+
- Agent `/api/apps` no longer drops `FLAG_SYSTEM` apps (preloaded / updated-system ESPN was missing from Check packages / app picker)
- Manual Pair starts pairing when the modal opens so the PIN appears before Complete (Auto-pair unchanged)
- ESPN install-error copy: Fire / Amazon → `com.espn.gtv`; Google / Android TV → `com.espn.score_center`

## [0.1.15] - 2026-08-05

### Fixed
- Hybrid App Play `adbtuner_open_app` prefers **Agent** `/api/launch` again (Remote `send_launch_app_command` was opening the **Play Store** when `com.espn.gtv` was not installed, then D-pad ran on the store)
- Android TV Remote package launches fail if the foreground app is Play Store / an installer instead of the target package
- ESPN package family: auto-pick installed `com.espn.score_center` ↔ `com.espn.gtv`; App Play fails clearly when neither is installed
- **Full package fallback:** launch / `adbtuner_open_app` retries primary → `alternate_package_name` → ESPN family until one opens (even when the Agent package list is empty or stale); force-stop clears all candidates

### Added
- **Package coverage** dashboard: Channels → **Check packages** compares channel `package_name` to Agent/ADB installed apps; missing packages warn in the table and channel editor (searchable app list with Package / Alternate buttons)

## [0.1.14] - 2026-08-04

### Fixed
- Hybrid App Play `adbtuner_open_app` now opens the package on the **keys** plane (Remote / ADB monkey) instead of Agent `launch-intent` VIEW, which left the home launcher and caused random Selects / failed ESPN tunes
- Package-only Agent opens use `/api/launch` (launcher activity) instead of `/api/launch-intent`; Agent 4xx errors include the response message
- Preview D-pad / volume keys fail with a clear setup message when no Keys / D-pad backend is configured (Agent alone still supports Back / Home only)
- `firetv_rest` maps volume up/down for dashboard remote controls

### Added
- **Tuner encoder preview** (ADBTuner-style): Preview on each tuner card opens a modal with live HDMI encoder MJPEG (JPEG snapshot fallback) and remote controls (D-pad, Enter, Back, Home, volume, wake/sleep, reboot via ADB)

## [0.1.13] - 2026-08-03

### Fixed
- Agent `launch-intent` HTTP 4xx or `success: false` now fails the tune instead of reporting ready with a blank/wrong screen (ESPN App Play / bad package)
- Who’s-watching with `check_for_and_clear_whos_watching_prompts` fails the tune when no D-pad `keys_control` is configured (`skipped_no_dpad`) instead of opening the Max profile picker as “ready”

### Added
- Dashboard warning when Agent tuners lack a Keys / D-pad backend but channels need Max profile, App Play, or DPAD macros

## [0.1.12] - 2026-07-29

### Added
- **Hybrid `keys_control`**: keep `http_agent` for launches; optional `androidtv_remote` / `firetv_rest` / `adb` plane for D-pad (`key_macro`, App Play, who’s-watching)
- **Deep-link configuration overlays**: Compatibility / FDL configs with deeplink URLs apply pre/post commands and who’s-watching (App Play remains non-deeplink only)
- **Encoder who’s-watching OCR**: ffmpeg + tesseract when `check_for_and_clear_whos_watching_prompts` is true (~3.5s budget; timed DPAD fallback)
- **Dynamic / lane URL resolution**: ADBTuner-compatible fetch for FruitDeepLinks `/lanes/…` and `dynamic_url_json_key`

### Fixed
- `key_macro` entries like `DPAD_CENTER;DPAD_CENTER` now split into two Selects; DPAD macros fail clearly without a D-pad backend instead of a silent Agent no-op

## [0.1.11] - 2026-07-27

### Added
- **App Play configurations** (babsonnexus / ADBTuner): import configuration JSON, keep `configuration_uuid` on channels, and run D-pad scripts ADB-free via `androidtv_remote` or new `firetv_rest`
- **`firetv_rest` backend**: Fire TV Remote HTTP API (pin pair, D-pad, package launch) for App Play on Fire Stick / Fire TV without ADB
- **`adb` backend**: network ADB App Play fallback for Fire OS devices that lack the `:8080` Remote API (real force-stop / keyevents)
- Dashboard **Configurations** tab for import/export of App Play scripts

### Fixed
- Fire Stick **Grant permissions (ADB)**: if Accessibility is written to settings but not bound, reboot once and re-apply (Fire OS often needs a reboot for Send keys)
- Fire Stick docs / Agent UI: overlay, usage, notification, and Accessibility are **ADB-only** — no Settings → Accessibility fallback (Fire has no usable on-device grant path for sideloaded apps)

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

[Unreleased]: https://github.com/matthewfkoch/APITuner/compare/v0.1.28...HEAD
[0.1.28]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.28
[0.1.27]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.27
[0.1.26]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.26
[0.1.25]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.25
[0.1.24]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.24
[0.1.23]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.23
[0.1.22]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.22
[0.1.21]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.21
[0.1.20]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.20
[0.1.19]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.19
[0.1.18]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.18
[0.1.17]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.17
[0.1.16]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.16
[0.1.15]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.15
[0.1.14]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.14
[0.1.13]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.13
[0.1.12]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.12
[0.1.11]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.11
[0.1.10]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.10
[0.1.9]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.9
[0.1.8]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.8
[0.1.7]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.7
[0.1.6]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.6
[0.1.5]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.5
[0.1.4]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.4
[0.1.3]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.3
[0.1.2]: https://github.com/matthewfkoch/APITuner/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/matthewfkoch/APITuner/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/matthewfkoch/APITuner/releases/tag/v0.1.0
