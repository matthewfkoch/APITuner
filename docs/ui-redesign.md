# Control-room UI

The dashboard remains plain HTML/CSS/JavaScript and the Agent remains native Android Views. Backend APIs, configuration formats, playback, permission grants, and update installation behavior are unchanged.

## Identity

The soft lowercase wordmark uses fixed vector outlines traced from the selected concept artwork, including its connected t/u/n lettering. `wordmark.svg` is the deep teal master and `wordmark-light.svg` is the dashboard variant for dark surfaces. The dashboard displays the SVG directly, without a logo font dependency. `logo-mark.svg`, `logo.svg`, and `favicon.svg` use the wordmark’s actual lowercase a. Android vector drawables share the same outlines, including `brand_wordmark.xml` for the Agent header.

Use charcoal backgrounds, bright neutral text, teal actions/selection, green confirmed successes, amber attention, and red failures. Version metadata is neutral. The TV focused outline is white; the selected navigation outline is teal, so focus and selection stay distinguishable.

## Behavior

- Tuner rows separate health checks, current allocation, pairing, and historical errors. “In use” means the server reports a lock, not verified playback. Missing status data displays “Status unavailable.” Disabled devices remain visible without setup alerts.
- More expands technical details and maintenance actions. Attention links expand the affected row. Identical polling responses preserve the existing issue links and focus.
- Channels show package coverage without exposing raw URLs in the list. Unknown coverage never claims an app is available; all original fields remain in Edit.
- Connection URLs now live in Settings. Channel Tools offers package checking, sync, export, and playlist URLs. Native disclosure controls support keyboard activation.
- Dialogs label fields, contain keyboard focus, hide the background from accessibility, close with Escape, and restore focus to their opener (or the current navigation control if the opener was removed).
- Agent Overview reports required/recommended permission readiness only; it does not claim the encoder or DVR is connected. Permissions labels distinguish optional features. Back closes token editing before leaving Advanced, then returns to Overview.

## Validation

Automated checks from the repository root:

```sh
node --check server/apituner/web/app.js
node --test server/tests/web/ui-state.test.cjs
python -m pytest server/tests -q
cd agent
./gradlew :app:assembleDebug
```

The Python tests require a supported Python version (the verification run used 3.12). Results: 203 server tests and six UI-state tests passed; Android debug APK built successfully.

Browser verification used an isolated sample-data server, without changing real tuners:

- Empty, single-device, and 13-device views; active, offline, disabled, unpaired, missing D-pad, update available, and historical error states.
- 390, 768, 900, and 1440px layouts; labeled navigation and mobile connection URLs.
- Channel editing, grouped maintenance actions, device disclosures, dialog focus wrapping, Escape dismissal, and return focus.

Before shipping an Agent release, validate on physical Google TV and Fire TV: readability from normal seating distance, all D-pad routes, return from each system permission screen, token editing/cancel/save, installer return, and optional permissions. No physical TV or emulator was available for this implementation pass. Follow normal release versioning/signing procedures; this work does not install the APK or deploy the server.
