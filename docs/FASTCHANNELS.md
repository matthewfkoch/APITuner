# FastChannels Player integration — hardware proof and staging contract

## Connect FastChannels

Use a FastChannels build that includes **Bridge → External tuners**. FastChannels
v5.9.0 predates this feature. See the
[FastChannels external tuner guide](https://github.com/kineticman/FastChannels/blob/development/docs/external-tuner-api.md#turning-it-on)
for availability and details.

1. On the TV, install the updated APITuner Agent and the official FastChannels
   Player. Grant the Agent **Display over other apps** and **Notification Access**.
   Grant **Usage Access** so APITuner can tell when the Player leaves the
   foreground. In APITuner, configure the TV and HDMI encoder as an HTTP Agent tuner.
2. In FastChannels, switch to **Advanced** mode if **Bridge** is hidden. Open
   **Bridge → External tuners** and turn on **Allow external tuners**. Copy the
   server URL and generated token from that card. You can skip the FastChannels
   Player Devices, HDMI Capture, and ah4c setup screens.
3. Enable **Bridge mode** at the top of the FastChannels Bridge page. Run a scrape
   or **Stream Audit** on the sources you want to import so FastChannels marks the
   Player-compatible channels.
4. In APITuner, open **Options → Connect FastChannels**, paste the URL and token,
   and select **Connect & import**. Use the M3U and XMLTV URLs under
   **Connect to Channels DVR**. APITuner serves the XMLTV URL when HDHomeRun
   emulation is enabled.

If the import finds zero channels, check that the desired sources and Bridge mode
are enabled, then scrape or audit those sources and retry. If you select **New
token** in FastChannels, paste the replacement into APITuner and connect again.
Refreshes preserve existing channel names, numbers and guide overrides. Missing
channels are hidden from M3U and HDHomeRun until they return.

For the older patched beta, the shared secret was created by the operator. See
the [original beta setup](RELEASE-0.1.35-fastchannels.1.md). Existing
environment-based APITuner connections can continue using `FASTCHANNELS_TOKEN`.

## Current status

The first hardware gate passed on October 28, 2026, on a Chromecast running
Android 14. APITuner Agent `0.1.35-dev` and the official FastChannels Player were
installed with ADB. Playback control then used the Agent HTTP API exclusively:

- Sky News from Samsung: launch, actual PLAYING state, and encoder video verified.
- Cheddar News: same-app channel switch and encoder video verified.
- ABC News Live from Sling Freestream: DASH/Widevine playback with `drm=true`,
  encoder video verified, and a 90-second observation ending in PLAYING.
- HTTP `warm_stop` reached a non-playing state, then HTTP HOME returned the device
  to its launcher. Original tuner availability was restored after each test.

The release-signed Agent update retained existing settings and permissions. The
original installed APK was backed up locally before installation. The Player
needs an explicit Android package query because it has no launcher entry; without
that query, the Agent can launch it but reports it missing from its app list.

The local APITuner source integration now includes manual/periodic authenticated
catalog sync, stable channel IDs and numbers, unavailable-channel filtering,
XMLTV mapping, and HTTP-only tuner lifecycle management. Each tune receives a
unique Player channel key; cleanup retains the tuner reservation until stopped.
FastChannels streams are always proxied so disconnects release their reservation.
During an interruption, APITuner gives the Player up to 130 seconds to recover.
The shorter 15-second grace still applies when the Player leaves the foreground,
stops, or cannot be reached. Encoder reads are bounded.

The integrated staging deployment passed end-to-end validation on November 5,
2026. APITuner runs at `http://192.168.1.19:6593`, and its companion runs at
`http://192.168.1.19:5524`, both in Portainer stack `apituner-fc-staging` with
separate data volumes. Production services have not been replaced.

The shared test tuner is disabled in staging after tests, preventing competing
allocation with production. Its original production enabled state was restored.
To test again, reserve it exclusively before enabling it in staging. This staging
companion intentionally has no scheduler; its test channel guide was refreshed
manually. A normal deployment uses the upstream scheduler to maintain guide data.

## Initial patched server tests

The staging server passed authenticated catalog and prepare requests for one
Sling Freestream channel (ABC News Live), and its DASH manifest returned 200.
That prepared request launched the Chromecast through HTTP; the Agent reported
PLAYING and the HDMI encoder showed ABC News Live video. On resuming October 29,
the device was verified non-playing and its tuner enabled/unlocked without an
error. The test process's final output was no longer available, so this is not
recorded as a verified sustained-duration result.

## Lease cleanup hardening (local code)

Release now retains ownership until cleanup completes. Duplicate releases share
one cleanup task, client cancellation does not cancel cleanup, and old lease
releases cannot stop a newer tune. Cleanup has a 60-second timeout; errors retain
the reservation for retry instead of exposing the device for another tune.
The stale-lease reaper and shutdown use the same cleanup path. Canceled or failed
tune acquisition attempts stop their device before returning the reservation.
These changes are not deployed to the production APITuner server yet. Unique Player keys protect conditional stops. First-frame detection and
reconciliation after a process crash are not provided.

## Agent changes

`POST /api/launch-intent` accepts optional `intent_extras`, an object whose values
must be strings or booleans. It preserves URL punctuation and actual Android
boolean extras. Legacy `extra_string` and `extra_*` remain supported; structured
values win on duplicate keys. Unsupported values fail before launch.

`GET /api/info` and `/api/diagnostics` advertise `typed_intent_extras=true`.
The Python HTTP backend checks that capability before sending structured extras.
Hybrid control forwards typed launches only through the primary Agent; it does
not fall back to a remote/ADB launch.

The legacy playback boolean continues to include buffering for compatibility.
The probe reads the detailed `state` field and requires `playing`, not `buffering`.
Package-level playback still cannot identify a particular channel or confirm the
first decoded frame. Encoder inspection is necessary during this prototype.

## Repeatable HTTP-only probe

Use Python 3.11+ with the server dependencies installed. Install the updated Agent
and the official FastChannels Player on a test device first. Grant overlay and
Notification Access. The device must be awake.

From `server/`:

```sh
python -m apituner.fastchannels_probe_cli \
  --apituner-url http://APITUNER_HOST:6592 \
  --fastchannels-url http://FASTCHANNELS_HOST:5523 \
  --tuner-id YOUR_TUNER_ID \
  --channel-id FASTCHANNELS_DATABASE_ID \
  --seconds 90 \
  --snapshots /tmp/fastchannels-proof
```

Repeat `--channel-id` to test switches. IDs can be read from FastChannels' admin
channel API. This explicit probe mode uses the existing **admin preview API**,
which may require login on secured installations. It is not the production
contract and it does not change channel enablement or provider settings.

The probe checks the tuner is idle, disables it for ordinary automatic allocation,
launches via HTTP, captures optional encoder JPEGs, and confirms HTTP warm-stop
before restoring the original enabled flag. If cleanup cannot be confirmed it
leaves the tuner disabled and reports why. Do not run two probes concurrently or
edit this tuner during a probe: older production servers lack atomic maintenance
leases. Do not kill the process; a hard kill can leave the tuner disabled/playing.

## Companion server API

The staged patch and standalone endpoint tests are in `integrations/fastchannels/`.
This patch is disabled unless `FC_EXTERNAL_PLAYER_TOKEN` is configured. It uses
its own bearer token even when the FastChannels admin UI requires browser login.
It exposes catalog, playback preparation and XMLTV, and never controls a device.

To probe that contract after deploying the staging patch, set `FASTCHANNELS_TOKEN`
in the environment and replace `--channel-id` with
`--source-channel SOURCE:UPSTREAM_CHANNEL_ID`. The client does not follow API
redirects, does not fall back to preview, checks the fixed Player component,
and only forwards recognized playback extras.

## Validation on November 4, 2026

- Full Python suite: **264 passed**, with five dependency deprecation warnings.
  One existing sub-second YouTube TV timing test failed on the first run, then
  passed individually and in the full rerun.
- Updated companion patch applies to deployed revision `b3c19be946aa978e8a03538e2ee2f3583f23e630`.
- Companion image `apituner-fastchannels:staging-final` built locally. Its real
  Flask/ORM smoke test passed with networking disabled: auth, catalog eligibility,
  typed preparation, unavailable rejection, XMLTV IDs/programmes and empty guide.
- APITuner image `apituner:fastchannels-staging` built locally. Isolated container
  startup, status, source options and unconfigured-sync error handling passed.
- JavaScript syntax and patch whitespace checks passed.

## End-to-end validation on November 5, 2026

Both candidates were built and deployed through Portainer. Authenticated catalog
sync imported ABC News Live from Sling Freestream. Repeat sync added no duplicates
and preserved its channel ID. Current Sling programmes reached APITuner XMLTV
with matching playlist IDs; unauthenticated companion guide requests returned 401.

Three consecutive stream cycles passed: readiness took 4.79, 1.58 and 1.59 seconds.
Each delivered video transport and stopped the Player/unlocked the tuner after
client disconnect. A fourth tune captured actual ABC News Live encoder video.
An injected HTTP Player stop ended the active stream within 19 seconds and
released the tuner. A disconnect 250 milliseconds into startup also cleaned up
successfully. All playback operations used HTTP; no ADB commands were used.
The failure-test harness initially used the wrong package field; the corrected
harness passed. Production availability was confirmed restored after testing.

Results are recorded in `fastchannels-validation-2026-11-05.json`.

## Production rollout and remaining qualification

The implementation and single-device staging acceptance tests are complete.
Production rollout remains separate: back up APITuner configuration and the
FastChannels database, deploy the compatible companion image with its scheduler,
configure the service token, and deploy the APITuner candidate. Retain previous
image tags and backups for rollback. Do not enable the same device in two servers.

Multiple-device concurrency, overnight recordings, provider licence renewal and
operation with Android ADB disabled have not been qualified. Package-level
MediaSession state cannot prove a particular channel's first video frame;
encoder video was inspected during these tests. YouTube TV's automated regression
tests passed; switching to a configured YouTube TV channel on hardware was not
retested because the existing channels require App Play configurations absent
from isolated staging.

Sling Freestream required no paid subscription. The result qualifies the tested
channel and device, not every provider, channel or subscription entitlement.
