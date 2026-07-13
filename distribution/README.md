# APITuner

APITuner is a virtual tuner for [Channels DVR](https://getchannels.com/). It controls Android TV / Google TV devices and relays each device's HDMI-encoder stream to Channels as a Custom Channels (M3U) source.

Download the latest **Agent APK** from [Releases](https://github.com/matthewfkoch/APITuner-releases/releases).

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

Open the dashboard at `http://<docker-host>:6592`.

**Server basics**

- Web dashboard on port **6592** — manage tuners, channels, and options
- **M3U playlist** at `/channels.m3u` — add this URL in Channels DVR as a Custom Channels source
- Each **tuner** is one Android TV device plus its HDMI encoder stream URL
- When Channels requests a channel, APITuner launches the app on a free tuner and relays MPEG-TS from the encoder

Use APITuner on a trusted LAN only. The dashboard has no login.

---

## Agent APK

Install `apituner-agent-<version>.apk` on each Android TV / Google TV device.

1. Open the app and grant **Display over other apps** (required), **Usage Access** (recommended), and **Notification Access** (optional).
2. In the APITuner dashboard, click **Add tuner**:
   - Backend: `http_agent`
   - Device IP and port: `9092`
   - Stream URL: your HDMI encoder URL for that device

**Agent basics**

- Runs on the TV and receives tune commands from the server
- Launches streaming apps with package-pinned deep links (reliable for YouTube TV and similar apps)
- Reports foreground app and playback state so the server knows when tuning is ready

Each device needs its own HDMI encoder. APITuner does not capture the screen inside the app — it relays the encoder's MPEG-TS stream.
