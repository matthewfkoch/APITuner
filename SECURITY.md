# Security

## Dashboard and API

APITuner exposes an **unauthenticated** web dashboard and REST API on port **5593**. Anyone who can reach that port can:

- Add, edit, or delete tuners and channels
- Change global tuning options
- Trigger pairing flows
- Start streams (which launches apps on connected devices)

**Do not expose port 5593 to the public internet.** Run APITuner on a trusted LAN, or place it behind a VPN / reverse proxy with authentication.

## Agent APK

The APITuner Agent listens on port **9092** on each Android device. Optional token auth is supported via the `X-Auth-Token` header, but no on-device UI configures it yet — treat the agent as trusted-LAN only.

Grant only the permissions you need:

- **Display over other apps** — required to launch apps from the background
- **Usage Access** — foreground-app detection for tune readiness
- **Notification Access** — optional, improves playback detection
- **Accessibility** — optional, BACK/HOME/RECENTS keys only

## Reporting issues

Open a [GitHub issue](https://github.com/matthewfkoch/APITuner/issues) for security concerns. Do not commit `data/` (config, pairing certs) to the repository.
