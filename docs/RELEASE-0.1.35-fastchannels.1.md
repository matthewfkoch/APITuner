# APITuner 0.1.35-fastchannels.1

Opt-in FastChannels prerelease, separate from the stable release and main branch.

- Authenticated catalog sync with stable channel IDs and numbers.
- XMLTV guide mapping and HTTP-only FastChannels Player tuning.
- Per-tune stop ownership, playback monitoring and disconnect cleanup.
- Updated Android Agent with typed intent extras (version code 34).

Use `ghcr.io/matthewfkoch/apituner:0.1.35-fastchannels.1` explicitly.
This prerelease does not update Docker `latest` or the stable Agent update feed.
Install the attached signed Agent APK manually. A later stable Agent must use a
version code greater than 34 to upgrade devices running this prerelease.

Requires the official FastChannels Player and the attached companion patch,
validated against FastChannels revision b3c19be946aa978e8a03538e2ee2f3583f23e630.
Configure matching FC_EXTERNAL_PLAYER_TOKEN / FASTCHANNELS_TOKEN environment
variables on the companion / APITuner servers. Save the companion URL in
APITuner Options and select Sync FastChannels. See FASTCHANNELS.md for setup.

Validated: 264 automated tests, Sling Freestream video, guide mapping, repeated
tunes, playback-loss handling and disconnect cleanup on one Chromecast.
Multi-device and overnight recording qualification remain outstanding.
Back up configuration and database before installing. Keep existing stable image
and data backups for rollback. Do not allocate the same device from two servers.
