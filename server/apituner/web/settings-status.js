"use strict";

// Settings (Options), FastChannels connect form, Diagnostics download, and Live Status tab.

// ============================ OPTIONS ============================
const OPTION_FIELDS = [
  ["tune_timeout_seconds", "Tune timeout (s)", "number", null, "How long to wait for a channel to become ready"],
  ["request_timeout", "Request timeout (s)", "number", null, "HTTP timeout for Agent API calls"],
  ["dynamic_url_timeout", "Lane URL timeout (s)", "number", null, "Per try for /whatson and FruitDeepLinks. Hung connects stop at 5s; a hung read can still be timeout × attempts (15 × 3 = 45s). Stay near Channels' ~30s window. Applies on next tune."],
  ["dynamic_url_attempts", "Lane URL attempts", "number", null, "Tries on timeout or connection error (1–10, default 3). Hung reads add another full timeout each try."],
  ["release_grace_seconds", "Release grace (s)", "number", null, "Hold the tuner briefly after the stream disconnects"],
  ["stuck_tuner_timeout_seconds", "Stuck tuner timeout (s)", "number", null, "Reclaim tuners that stop making progress"],
  ["tuner_idle_timeout_seconds", "Idle reclaim (redirect) (s)", "number", null, "Reclaim tuners in redirect mode after idle"],
  ["stream_mode", "Stream mode", "select", ["proxy", "redirect"], "proxy relays the encoder; redirect sends clients to it"],
  ["stream_during_tune", "Stream during tune", "bool", null, "Start the encoder while App Play or deeplink playback wait runs so Channels does not time out"],
  ["encoder_recovery_seconds", "Encoder recovery (s)", "number", null, "How long proxied streams wait for encoder video before ending (0–60 seconds, 0 disables recovery)"],
  ["app_play_prefer", "App Play stick preference", "select", [
    { value: "fire", label: "Fire Stick / ADB first" },
    { value: "google_tv", label: "Google TV / Chromecast first" },
    { value: "any", label: "No preference" },
  ], "When several tuners can run App Play, prefer this path"],
  ["wait_for_playback", "Wait for playback signal", "bool", null, "Wait until playback is detected before accepting the tune"],
  ["deeplink_relaunch_seconds", "Deeplink relaunch (s)", "number", null, "If playback never starts, wait this long then re-send the channel intent (DirecTV up to twice; 0 = off). Cold start retries after 2s. Helps after device reboot. Needs Notification access."],
  ["ready_settle_seconds", "Ready settle (s)", "number", null, "Extra wait after playback before opening the HDMI stream"],
  ["stop_on_release", "Stop app on release", "bool", null, "Send Home when the stream ends"],
  ["keep_apps_running", "Keep apps running", "bool", null, "When off, always send Home on release"],
  ["retry_on_other_tuner", "Retry on another tuner", "bool", null, "Try another tuner if a tune fails"],
  ["hdhr_enabled", "HDHomeRun emulation", "bool", null, "Appear as an HDHomeRun tuner in Channels DVR"],
  ["hdhr_friendly_name", "HDHomeRun name", "text", null, "Name shown in the Channels / Plex source list"],
  ["hdhr_device_id", "HDHomeRun DeviceID", "text", null, "Stable 8-character hex ID — do not change lightly"],
  ["hdhr_port", "HDHomeRun port (optional)", "number", null, "Leave blank to use the main APITuner port"],
  ["hdhr_ssdp_enabled", "SSDP discovery", "bool", null, "SSDP/UPnP multicast (needs host networking in Docker)"],
  ["hdhr_udp_discovery_enabled", "UDP 65001 discovery", "bool", null, "SiliconDust discovery (needs host networking in Docker)"],
  ["channels_dvr_url", "Channels DVR URL", "text", null, "LAN URL, e.g. http://192.0.2.30:8089"],
  ["xmltv_source_device", "XMLTV source device", "text", null, "Channels device ID used as the schedule source"],
  ["xmltv_duration_seconds", "XMLTV duration (s)", "number", null, "How far ahead to pull listings (default 3 days)"],
  ["xmltv_cache_seconds", "XMLTV cache (s)", "number", null, "How long to reuse a built XMLTV response"],
  ["fastchannels_start_number", "FastChannels first number", "number", null, "New channels start here; existing channel numbers are preserved."],
  ["fastchannels_sync_seconds", "FastChannels refresh interval (s)", "number", null, "0 = refresh with Connect & import."],
  ["fruitdeeplinks_url", "FruitDeepLinks URL", "text", null, "LAN URL. Sync imports lanes; XMLTV remaps their guide"],
  ["fruitdeeplinks_profile", "FruitDeepLinks device profile", "select", [
    { value: "google_tv", label: "Google TV / Android TV" },
    { value: "fire", label: "Fire TV" },
  ], "Primary package on sync (ESPN differs on Fire vs Google TV)"],
  ["fruitdeeplinks_start_number", "FruitDeepLinks start number", "number", null, "First channel number for synced lanes (default 9000)"],
  ["fruitdeeplinks_xmltv_path", "FruitDeepLinks XMLTV path", "text", null, "Path on the FDL server (default /out/adb_lanes.xml)"],
  ["fruitdeeplinks_sync_seconds", "FruitDeepLinks auto-sync (s)", "number", null, "0 = manual Sync only"],
];

const optionsForm = document.getElementById("options-form");
const saveOptionsButton = document.getElementById("save-options");
let optionsDirty = false;
let savingOptions = false;
optionsForm.addEventListener("input", () => { optionsDirty = true; });

async function loadOptions() {
  if (optionsDirty || savingOptions) return;
  let opts = {};
  try { opts = await api.get("/api/options"); } catch (e) { toast(e.message, true); return; }
  if (optionsDirty || savingOptions) return;
  const fcUrl = document.getElementById("fastchannels-url");
  if (!fcUrl.value) fcUrl.value = opts.fastchannels_url || "";
  optionsForm.innerHTML = "";
  const groups = {};
  for (const [id, title] of [["integrations", "Integrations"], ["tuning", "Tuning behavior"], ["relay", "Stream relay"]]) {
    const group = el(`<fieldset class="settings-group"><legend>${title}</legend><div class="form-grid"></div></fieldset>`);
    optionsForm.append(group); groups[id] = group.querySelector(".form-grid");
  }
  for (const [key, label, type, choices, hint] of OPTION_FIELDS) {
    const group = /^(hdhr_|channels_|xmltv_|fruitdeeplinks_|fastchannels_)/.test(key) ? groups.integrations
      : /^(stream_mode|stream_during_tune|encoder_recovery_seconds|release_grace_seconds|stuck_tuner_timeout_seconds|tuner_idle_timeout_seconds)$/.test(key) ? groups.relay : groups.tuning;
    if (type === "bool") {
      const row = el(`<div class="field checkbox"><input type="checkbox" name="${key}" ${opts[key] ? "checked" : ""} /><label>${label}</label></div>`);
      if (hint) row.appendChild(el(`<div class="hint">${hint}</div>`));
      group.appendChild(row);
    } else if (type === "select") {
      const f = el(`<div class="field"><label>${label}</label><select name="${key}"></select>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`);
      choices.forEach((ch) => {
        const value = typeof ch === "object" && ch ? ch.value : ch;
        const text = typeof ch === "object" && ch ? (ch.label || ch.value) : ch;
        const o = el(`<option value="${escapeAttr(value)}">${escapeHtml(text)}</option>`);
        if (opts[key] === value) o.selected = true;
        f.querySelector("select").appendChild(o);
      });
      group.appendChild(f);
    } else if (type === "text") {
      const val = opts[key] == null ? "" : opts[key];
      group.appendChild(el(`<div class="field"><label>${label}</label><input type="text" name="${key}" value="${escapeAttr(val)}" />${hint ? `<div class="hint">${hint}</div>` : ""}</div>`));
    } else {
      const val = opts[key] == null ? "" : opts[key];
      const limits = key === "hdhr_port" ? 'min="1" max="65535" step="1"'
        : key === "encoder_recovery_seconds" ? 'min="0" max="60" step="any"' : 'step="any"';
      group.appendChild(el(`<div class="field"><label>${label}</label><input type="number" ${limits} name="${key}" value="${val}" />${hint ? `<div class="hint">${hint}</div>` : ""}</div>`));
    }
  }
  labelFields(optionsForm);
  saveOptionsButton.disabled = false;
}

optionsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveOptionsButton.disabled) return;
  const payload = {};
  for (const [key, , type] of OPTION_FIELDS) {
    const input = optionsForm.elements.namedItem(key);
    if (!input) continue;
    if (type === "bool") payload[key] = input.checked;
    else if (type === "number") {
      payload[key] = key === "hdhr_port" && input.value === "" ? null : Number(input.value);
    } else payload[key] = input.value;
  }
  savingOptions = saveOptionsButton.disabled = true;
  optionsDirty = false;
  try {
    await api.put("/api/options", payload);
  } catch (e) {
    optionsDirty = true;
    toast(e.message, true);
    return;
  } finally {
    savingOptions = saveOptionsButton.disabled = false;
  }
  toast("Settings saved (restart required for discovery changes)");
  api.get("/api/status").then(initSidebarUrls).catch((e) => toast(e.message, true));
});

document.getElementById("download-diagnostics").addEventListener("click", () => {
  window.location.href = "/api/diagnostics";
  toast("Downloading diagnostics…", false, 2500);
});

// ============================ STATUS ============================
let statusTimer = null;
function startStatusPolling() { stopStatusPolling(); renderStatus(); statusTimer = setInterval(renderStatus, 3000); }
function stopStatusPolling() { if (statusTimer) { clearInterval(statusTimer); statusTimer = null; } }

async function renderStatus() {
  const list = document.getElementById("status-list");
  const stats = document.getElementById("status-stats");
  let data;
  try { data = await api.get("/api/status"); } catch (e) {
    list.innerHTML = `<div class="empty-state"><p>${escapeHtml(e.message)}</p></div>`;
    stats.innerHTML = "";
    return;
  }

  const active = data.tuners.filter((t) => t.locked).length;
  const free = data.tuners.filter((t) => t.enabled !== false && !t.locked).length;
  const errors = data.tuners.filter((t) => t.last_error).length;

  const hdhrBits = data.hdhr && data.hdhr.enabled
    ? ` · HDHR ${data.hdhr.tuner_count} tuner${data.hdhr.tuner_count === 1 ? "" : "s"}`
    : "";
  document.getElementById("status-meta").textContent =
    `v${data.version} · ${data.options.stream_mode} stream mode${hdhrBits}`;

  document.getElementById("app-version").textContent = `v${data.version}`;
  initSidebarUrls(data);

  stats.innerHTML = `
    <div class="stat-card"><div class="stat-label">Tuners</div><div class="stat-value">${data.tuners.length}</div></div>
    <div class="stat-card"><div class="stat-label">Active</div><div class="stat-value accent">${active}</div></div>
    <div class="stat-card"><div class="stat-label">Available</div><div class="stat-value green">${free}</div></div>
    <div class="stat-card"><div class="stat-label">Channels</div><div class="stat-value accent">${data.channel_count}</div></div>
    ${errors ? `<div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value" style="color:var(--red)">${errors}</div></div>` : ""}`;

  if (!data.tuners.length) {
    list.innerHTML = `<div class="empty-state"><div class="empty-state-icon">●</div><h3>No tuners</h3><p>Add tuners to see live activity here.</p></div>`;
    return;
  }
  list.innerHTML = "";
  for (const s of data.tuners) {
    const activity = s.locked ? "In use" : s.enabled === false ? "Disabled" : "Idle";
    const card = el(`<article class="card ${s.locked ? "card-active" : ""}"></article>`);
    card.innerHTML = `
      <div class="card-head">
        <div>
          <div class="card-title">
            <span class="dot ${s.locked ? "locked pulse" : "free"}"></span>
            ${escapeHtml(s.name)}
          </div>
          <div class="card-sub">${s.backend}${s.model ? " · " + escapeHtml(s.model) : ""}</div>
        </div>
        <span class="badge ${s.locked ? "accent" : ""}">${activity}</span>
      </div>
      <div class="card-meta">
        ${s.locked ? `<div class="card-row"><span class="label">Channel</span><span class="value"><strong>${s.channel_number ?? "?"}</strong> · ${escapeHtml(s.channel_name || "")}</span></div>` : ""}
        ${s.last_tune_seconds != null ? `<div class="card-row"><span class="label">Last tune</span><span class="value">${s.last_tune_seconds.toFixed(1)}s</span></div>` : ""}
        ${s.locked && s.bytes_transferred ? `<div class="card-row"><span class="label">Streamed</span><span class="value">${fmtBytes(s.bytes_transferred)}</span></div>` : ""}
        ${s.locked && s.lock_seconds != null ? `<div class="card-row"><span class="label">Lock time</span><span class="value">${s.lock_seconds}s</span></div>` : ""}
        ${s.last_error ? `<div class="card-row"><span class="label">Error</span><span class="value"><span class="badge off">${escapeHtml(s.last_error)}</span></span></div>` : ""}
      </div>`;
    list.appendChild(card);
  }
}

document.getElementById("fastchannels-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.getElementById("fastchannels-url");
  const credential = document.getElementById("fastchannels-credential");
  const button = document.getElementById("connect-fastchannels-btn");
  const resultText = document.getElementById("fastchannels-result");
  button.disabled = url.disabled = credential.disabled = true;
  resultText.textContent = "Checking FastChannels…";
  try {
    const result = await api.post("/api/fastchannels/connect", { url: url.value.trim(), credential: credential.value });
    credential.value = "";
    resultText.textContent = result.available
      ? `${result.available} channels available (${result.added} new, ${result.unavailable} unavailable). Copy the M3U and XMLTV URLs from Connect to Channels DVR above.`
      : "Connected. No compatible channels found. Enable Bridge mode in FastChannels, then scrape or audit the sources you want to import.";
    api.get("/api/status").then(initSidebarUrls).catch(() => {});
  } catch (error) {
    resultText.textContent = error.message;
  } finally {
    button.disabled = url.disabled = credential.disabled = false;
  }
});
