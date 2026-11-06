# FastChannels external-player staging patch

This is the **resolve-only portion** of the planned integration, based on
FastChannels development commit `f9754b383d2322a79ee7e08943a8914518a5f849`.
The initial catalog/prepare version is deployed only to isolated staging.
The current patch also includes authenticated XMLTV; redeploy to enable it.
APITuner session management is implemented in the server integration.

## Verified staging deployment (2026-10-28)

Portainer's MacMini environment runs the original standalone `fastchannels`
container on port 5523. Its image revision is
`b3c19be946aa978e8a03538e2ee2f3583f23e630`, image digest
`sha256:3a821a3b011cf42bbb029ee19124b3d8949d703caa29de89f3eced72a23d91a3`.
Production mounts `fastchannels_data` at `/data` and `fastchannels_adb` at
`/root/.android`.

The patch also applies cleanly to that deployed revision. The staging image
`apituner-fastchannels:staging-b3c19be` overlays only the five patched Python
files from that revision onto the exact production image digest. It was built
locally and in Portainer. Stack `apituner-fc-staging` runs on
`192.168.1.19:5524` with its own fresh data volume; production is unchanged.
The user configured a staging-only API token. An unauthenticated request returns
401. No production database or ADB keys were copied.

Authenticated capabilities/catalog returned 200. One ABC News Live channel was
imported directly from Sling's free catalog into staging. Prepare returned typed
DRM extras with staging manifest/license URLs, and the DASH manifest returned
200. A Chromecast HTTP launch using this result reached PLAYING and actual ABC
News Live encoder video. Device cleanup and restored tuner availability were
verified on October 29. Sustained-duration completion output was unavailable
after resuming; long-recording qualification remains outstanding.

The local image passed `smoke_full_app.py` with networking disabled and a
temporary database: real app/auth middleware, ORM catalog filtering, shared
playback preparation, and unavailable-channel rejection. Provider resolution was
stubbed. Catalog and prepare reject pending-review channels and guide-only
sources. All 41 focused APITuner/companion tests passed.

Startup validation found a Redis readiness race. The compose file now waits up
to 30 seconds for Redis before schema setup; that fix was tested locally and
applied to the staging stack. A complete container startup test passed, including
503 when no external API token is configured.

## Apply and test

In a separate FastChannels checkout at the recorded commit:

```sh
git apply --check /path/to/APITuner/integrations/fastchannels/external-player-v1.patch
git apply /path/to/APITuner/integrations/fastchannels/external-player-v1.patch
```

Use the upstream Dockerfile to build a separately tagged staging image. In
Portainer, preserve the existing image reference and volume configuration for
rollback; set `FC_EXTERNAL_PLAYER_TOKEN` to a generated secret on the staging
container. Do not run a second scheduler against the production database. Test
with an isolated data copy before replacing the live image.

`compose.staging.yaml` is the reusable Portainer staging configuration. It
requires a separately built patched image (`FC_STAGING_IMAGE`), the staging
host's LAN address (`FC_STAGING_BIND_IP`), and a staging-only token. Deploy it
as a new stack named `apituner-fc-staging`; it publishes port 5524 and uses a
stack-scoped data volume. Verify that port is available before deployment.
The overridden entrypoint runs schema setup and the web process, but does not
start upstream's scheduler, scraping or maintenance workers. Queued UI scrape
actions therefore will not run in this staging mode.

The initial volume is empty. Populate it only with a consistent SQLite backup
(SQLite backup API or a stopped source), not a raw copy of a live WAL database.
Never substitute the production data volume or production `DATABASE_URL`.
Before starting a copied database, clear legacy bridge/device settings in the
copy and verify its public/base URL settings point at staging. Do not copy ADB
keys. Provider credentials in a database backup remain private deployment data;
keep the backup out of Git. Backup/import testing remains pending. The current
staging database contains only the explicitly imported Sling test channel.

Standalone endpoint tests (no provider credentials or database required):

```sh
python -m pip install flask pytest
python -m pytest integrations/fastchannels/test_external_player.py
```

`external_player.py` is included for review and standalone tests; the patch is the
installation artifact and includes blueprint registration, authentication routing,
and the shared playback preparation helper. The tests validate the endpoint
contract; they do not validate the full application or upstream ORM query.

## Contract

All endpoints use `Authorization: Bearer <FC_EXTERNAL_PLAYER_TOKEN>` and return
`Cache-Control: no-store`. No token means disabled (503), missing/incorrect token
means 401. Browser admin cookies do not authorize these endpoints.

- `GET /api/external-player/v1/capabilities`: version 1, catalog and prepare
  capabilities, `controls_devices=false`.
- `GET /api/external-player/v1/channels`: enabled, active, non-retired channels
  from supported Player providers which are DASH or marked bridge-required.
  Includes source/channel identity, name, XMLTV ID, Gracenote ID, logo and group.
  Both guide modes are represented. No legacy ADB bridge configuration is needed.
- `GET /api/external-player/v1/xmltv`: native guide for the same eligible catalog.
- `POST /api/external-player/v1/prepare`: JSON containing only `source` and
  `channel_id`. Returns the fixed Player package/component and typed playback
  extras. It never accepts a device address, arbitrary URL, or ADB command.

Preparation is shared with the existing bridge route, including Amazon's paired
HD manifest/license handling. The existing ADB route remains available. The new
route does not invoke it, register a device, or add legacy ADB-watchdog tracking.
Unknown/disabled channels return 404, retired channels return 410, and resolution
failures return a sanitized 502.

## Remaining work before production

- Real-provider tests against staging, plus migration tests with an isolated backup.
- Validate unique per-tune Player keys and conditional cleanup on hardware.
- Per-session recovery ownership, same-channel concurrency and licence refresh tests.
- Deploy and validate the local APITuner source settings, sync, EPG mapping and lifecycle integration.

Do not interpret successful `/prepare` as proof that a device is playing. The
caller must launch the Player, verify playback and own its cleanup independently.

## Latest local validation (2026-11-04)

The updated patch applies cleanly to the deployed `b3c19be` revision. The local
`apituner-fastchannels:staging-final` image passed the real-app smoke test with
networking disabled, including authenticated XMLTV programme/identity checks
and an empty eligible catalog. The APITuner candidate
`apituner:fastchannels-staging` passed isolated container startup/API checks.
The combined Python suite passed 264 tests. These new images have not yet been
deployed to the remote staging stack.

## Integrated staging deployed (2026-11-05)

The stack now runs `apituner-fastchannels:staging-final` (image `88be596a31e9`)
on port 5524 and `apituner:fastchannels-staging` (image `79a8bef49244`) on port
6593. Each has its own data volume; the existing staging token is supplied to
both services through their respective environment variables. Production remains
unchanged. The shared Chromecast is disabled in staging after testing and enabled
in production.

End-to-end catalog, repeat sync, current programme guide mapping, three tune/stop
cycles, encoder video, playback-loss detection and startup-disconnect cleanup
passed. See `docs/FASTCHANNELS.md` for measured results and remaining qualification.
The reusable compose file now includes both services. The companion deliberately
runs without a scheduler in this isolated test setup.
