# APITuner 0.1.35-fastchannels.1

Opt-in FastChannels prerelease, separate from the stable release and main branch.

**The original beta was tested with a patched FastChannels server.** For builds
that include the **Connect & import** screen and FastChannels' built-in
**External tuners** feature, see the [current setup guide](FASTCHANNELS.md).
The steps below describe the original patched beta.
The APITuner Agent update and official FastChannels Player APK alone are insufficient.
Create your own shared secret for the beta. Set it as `FC_EXTERNAL_PLAYER_TOKEN`
on the patched FastChannels server and `FASTCHANNELS_TOKEN` on the APITuner server.
Stock FastChannels does not issue this token.

Set up the Android TV device and HDMI encoder in APITuner using an HTTP Agent tuner
and grant the Agent Notification Access. Sideload the official FastChannels Player
on the same device. For APITuner, skip FastChannels' Player Devices and HDMI
Capture setup. Those controls belong to FastChannels' ADB bridge.

- Authenticated catalog sync with stable channel IDs and numbers.
- XMLTV guide mapping and HTTP-only FastChannels Player tuning.
- Per-tune stop ownership, playback monitoring and disconnect cleanup.
- Updated Android Agent with typed intent extras (version code 34).

Use `ghcr.io/matthewfkoch/apituner:0.1.35-fastchannels.1` explicitly.
This prerelease does not update Docker `latest` or the stable Agent update feed.
Install the attached signed Agent APK manually. A later stable Agent must use a
version code greater than 34 to upgrade devices running this prerelease.

The attached companion patch was validated against FastChannels revision
`b3c19be946aa978e8a03538e2ee2f3583f23e630`. In this version, save the patched
FastChannels server URL in APITuner Options, then select **Sync FastChannels**.
Copy APITuner's M3U and XMLTV URLs into Channels DVR. See FASTCHANNELS.md for setup.

Validated: 264 automated tests, Sling Freestream video, guide mapping, repeated
tunes, playback-loss handling and disconnect cleanup on one Chromecast.
Multi-device and overnight recording qualification remain outstanding.
Back up configuration and database before installing. Keep existing stable image
and data backups for rollback. Do not allocate the same device from two servers.
