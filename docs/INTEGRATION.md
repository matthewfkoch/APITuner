# Integrating deeplink sources with APITuner

APITuner is an HDHomeRun-style virtual tuner for [Channels DVR](https://getchannels.com/). It launches **package-pinned Android intents** on Google TV / Android TV / Fire TV sticks (via the Agent APK), then relays the paired HDMI encoder.

This document is the contract for **FruitDeepLinks** and similar Android/Google TV deeplink aggregators.

## Recommended path

```
Channels DVR  --HDHomeRun-->  APITuner  --Agent launch-intent-->  stick
                     |              |
                     |              +-- GET resolver --> FruitDeepLinks
                     +-- Custom URL /xmltv.xml (FDL guide remapped to channel numbers)
```

Use **`http_agent`** (Agent APK) on the stick. Scheme URLs such as `sportscenter://…` and `aiv://…` must be sent with an explicit package. The Android TV Remote backend cannot pin a package and often shows “Open with”.

Do **not** point Channels at FruitDeepLinks `STRMLINK` M3U for these sticks — that bypasses APITuner and the HDMI encoder.

Use FruitDeepLinks **Android / Fire ADB** playlists (`/m3u/adb` or `/api/adb/lanes/…/deeplink`), not `?profile=apple`. APITuner launches whatever URI the resolver returns; it does not convert Apple HTTPS links into Android intents.

## Channel JSON (preferred)

`POST /api/import` accepts an ADBTuner-compatible array (or `{ "channels": [ … ] }`).

```json
{
  "number": 9001,
  "name": "ESPN Lane 1",
  "provider_name": "sportscenter",
  "package_name": "com.espn.score_center",
  "alternate_package_name": "com.espn.gtv",
  "url": "http://192.0.2.40:6655/api/adb/lanes/sportscenter/1/deeplink?format=json&dynamic_url_json_key=deeplink_url",
  "action": "android.intent.action.VIEW",
  "source": "fruitdeeplinks"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `number` | yes | Unique across the APITuner lineup. `sort_order` is used if `number` is null. |
| `name` | yes | Guide / dashboard label. |
| `package_name` | yes | Android application id launched with the intent. |
| `alternate_package_name` | no | Tried if the primary package is missing (ESPN Google vs Fire). |
| `url` | yes | Resolver URL **or** a static deeplink. Resolvers are fetched at tune time; the stored URL is not replaced. |
| `action` | no | Default `android.intent.action.VIEW`. |
| `source` | no | `fruitdeeplinks` lets **Sync** replace only this group (YouTube TV / App Play rows stay). |
| `component`, `key_macro`, `configuration_uuid`, `tvc_guide_stationid` | no | Same meaning as ADBTuner import. |

`GET /api/export` returns the same shape, including `source`.

## Dynamic / lane URLs

At tune time, APITuner fetches the channel `url` when it looks like a resolver:

- path contains `/lanes/` or `/whatson/`
- query has `dynamic_url_json_key`
- path looks like a deeplink API (`deeplink` or `/api/`) **and** `format=json|text`

JSON bodies are read with `dynamic_url_json_key` if set, then `deeplink`, `deeplink_url`, `url`. Empty bodies, `none` / `null`, or `{ "ok": false }` fail the tune with **no event on this lane**.

Prefer:

```
http://<fdl-host>:6655/api/adb/lanes/<provider>/<n>/deeplink?format=json&dynamic_url_json_key=deeplink_url
```

M3U import and FruitDeepLinks sync append those query params when missing.

## M3U import

`POST /api/import` also accepts:

```json
{ "m3u": "#EXTM3U\n…", "profile": "google_tv", "start_number": 9000, "replace": false }
```

or `{ "url": "http://192.0.2.40:6655/m3u/adb", "profile": "fire", "start_number": 9000 }`.

Optional `#EXTINF` tags: `package-name`, `alternate-package-name`, `tvg-chno`. Otherwise packages come from `GET /api/deeplink-catalog` using `/api/adb/lanes/{provider}/…` or the URL scheme/host. Rows with no mapping are skipped (`skipped` in the response).

`profile`: `google_tv` (default) or `fire`. ESPN primary package is `com.espn.score_center` on Google TV and `com.espn.gtv` on Fire; the other is always stored as alternate.

## Live FruitDeepLinks sync

Live **Sync FruitDeepLinks** tries, in order:

1. `GET /api/adb/lanes` (per-provider ADB lanes, if exported)
2. `GET /m3u/adb`
3. `GET /api/lanes` + `/whatson/{id}` (v2 virtual lanes — this is what most current FDL servers expose)
4. `GET /m3u/lanes` (rewrites `/lane/N/stream.m3u8` HLS URLs into `/whatson/N` resolvers; do not launch the HLS URL on a stick)

Virtual lanes mix apps (ESPN, Prime, Apple TV, …). At tune time APITuner infers the Android package from the resolved deeplink.

`fruitdeeplinks_sync_seconds` > 0 enables a background refresh (minimum 30s sleep between runs).

## XMLTV

When FruitDeepLinks URL is set, `/xmltv.xml` fetches FDL XMLTV (`fruitdeeplinks_xmltv_path`, default `/xmltv/adb`, then `/xmltv/lanes`) and rewrites `ADB-{provider}-{lane}` channel ids onto APITuner **channel numbers**. Gracenote remap from Channels DVR still applies to stations that have `tvc_guide_stationid`.

Point the HDHomeRun source’s guide provider at `http://<apituner>:6592/xmltv.xml`.

## Package catalog

`GET /api/deeplink-catalog` returns provider codes, URL schemes, HTTPS hosts, and Google TV / Fire packages. FruitDeepLinks can emit APITuner JSON using this map instead of guessing application ids.

## Agent on the stick

1. Install the Agent APK; grant overlay (required), usage, notifications.
2. Add a tuner with `http_agent` on port `9092` and an HDMI encoder URL.
3. For Max who’s-watching / D-pad, add hybrid `keys_control` (`androidtv_remote` on Google TV, `firetv_rest` or `adb` on Fire).
