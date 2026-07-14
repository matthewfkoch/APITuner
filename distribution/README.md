# APITuner

An **ADB-free** virtual tuner for [Channels DVR](https://getchannels.com/). APITuner controls networked Android TV / Google TV devices and relays each device's HDMI-encoder stream to Channels — preferably as an **HDHomeRun-compatible tuner** (Tuner Sharing / multi-TV sync), or as a Custom Channels (M3U) source.

- **Source:** [matthewfkoch/APITuner](https://github.com/matthewfkoch/APITuner)
- **Agent APK downloads:** [Releases](https://github.com/matthewfkoch/APITuner-releases/releases)
- **Docker image:** `ghcr.io/matthewfkoch/apituner:latest`

---

## Server (Docker)

```bash
mkdir -p apituner-data
docker run -d \
  --name apituner \
  -p 6592:6592 \
  -v "$(pwd)/apituner-data:/data" \
  --restart unless-stopped \
  ghcr.io/matthewfkoch/apituner:latest
```

For HDHomeRun auto-discovery (SSDP / UDP 65001), prefer host networking:

```bash
docker run -d \
  --name apituner \
  --network host \
  -v "$(pwd)/apituner-data:/data" \
  --restart unless-stopped \
  ghcr.io/matthewfkoch/apituner:latest
```

Open the dashboard at `http://<docker-host>:6592`.

**Server basics**

- Web dashboard on port **6592** — manage tuners, channels, and options
- **Recommended:** HDHomeRun source in Channels DVR (device URL shown in the dashboard) — enables **Tuner Sharing**
- **Alternate:** M3U playlist at `/channels.m3u` as a Custom Channels source
- Optional XMLTV guide at `/xmltv.xml` (Custom URL) when using HDHomeRun
- Each **tuner** is one Android TV device plus its HDMI encoder stream URL

Use APITuner on a trusted LAN only. The dashboard has no login. See [SECURITY.md](https://github.com/matthewfkoch/APITuner/blob/main/SECURITY.md) in the source repo.

---

## Agent APK

Install `apituner-agent-<version>.apk` on each Android TV / Google TV device.

1. Open the app and grant:
   - **Display over other apps** — required
   - **Usage Access** — recommended
   - **Notification Access** — optional
   - **Accessibility** — optional (BACK / HOME / RECENTS)
2. In the APITuner dashboard, click **Add tuner**:
   - Backend: `http_agent`
   - Device IP and port: `9092`
   - Stream URL: your HDMI encoder URL for that device

Later updates: use **Check for updates** in the Agent app, or **Update Agent** on the tuner card in the dashboard when a newer release is available. Confirm Install once on the TV remote.

**Agent basics**

- Runs on the TV and receives tune commands from the server
- Launches streaming apps with package-pinned deep links (reliable for YouTube TV and similar apps)
- Reports foreground app and playback state so the server knows when tuning is ready

Each device needs its own HDMI encoder. APITuner does not capture the screen inside the app — it relays the encoder's MPEG-TS stream.
