# Security

## Dashboard and API

APITuner exposes an **unauthenticated** web dashboard and REST API on port **6592**. Anyone who can reach that port can:

- Add, edit, or delete tuners and channels
- Change global tuning options
- Trigger pairing flows
- Start streams (which launches apps on connected devices)
- Call **Grant permissions (ADB)** (`POST /api/tuners/{id}/grant-permissions`), which uses network ADB to change special-access settings on a device that has already authorized this host’s ADB key (intended for Fire Stick setup)

**Do not expose port 6592 to the public internet.** Run APITuner on a trusted LAN, or place it behind a VPN / reverse proxy with authentication. Keep Fire TV **ADB debugging** off when you are not setting up permissions, or revoke unused ADB authorizations.

## Agent APK

The APITuner Agent listens on port **9092** on each Android device. Optional token auth is supported via the `X-Auth-Token` header and can be set in the Agent app's on-device settings — treat the agent as trusted-LAN only unless you enable and configure a token.

Grant only the permissions you need:

- **Display over other apps** — required to launch apps from the background
- **Usage Access** — foreground-app detection for tune readiness
- **Notification Access** — optional, improves playback detection
- **Accessibility** — optional, BACK/HOME/RECENTS keys only

## Reporting vulnerabilities

Prefer [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) on this repository when enabled (Settings → Code security → Private vulnerability reporting).

Otherwise open a [GitHub issue](https://github.com/matthewfkoch/APITuner/issues) **without** sharing exploit details or private configs publicly.

Do not commit `data/` (config, pairing certs), keystores, or agent tokens to the repository.
