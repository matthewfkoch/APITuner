"use strict";

// ---- API helpers ----
const api = {
  async get(path) { return handle(await fetch(path)); },
  async post(path, body) { return handle(await fetch(path, jsonOpts("POST", body))); },
  async put(path, body) { return handle(await fetch(path, jsonOpts("PUT", body))); },
  async del(path) { return handle(await fetch(path, { method: "DELETE" })); },
};
function jsonOpts(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
}
function formatDetail(detail) {
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      if (!e || typeof e !== "object") return String(e);
      const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
      return loc ? `${loc}: ${e.msg || "invalid"}` : (e.msg || JSON.stringify(e));
    }).join("; ");
  }
  return String(detail);
}
async function handle(resp) {
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!resp.ok) throw new Error(formatDetail(data && data.detail) || resp.statusText || "Request failed");
  return data;
}

// ---- Capability definitions (label + tooltip; optional live status from /api/info) ----
const CAP_DEFS = {
  http_agent: [
    { label: "Launch channels", hint: "Opens the streaming app and deep link when a station is tuned.", always: true },
    { label: "Foreground app", hint: "Detects which app is on screen after a tune. Requires Usage Access on the device.", cap: "current_app" },
    { label: "Playback check", hint: "Waits for a playing MediaSession before the HDMI stream is ready. Requires Notification Access.", cap: "playback_state" },
    { label: "Send keys", hint: "Sends BACK, HOME, and RECENTS through the Agent. Requires Accessibility on the device. Not full D-pad (App Play needs androidtv_remote, firetv_rest, or adb).", cap: "keys" },
    { label: "App list", hint: "Lists installed apps on the device — used when picking a package while editing channels.", cap: "app_list" },
    { label: "Install APKs", hint: "Can sideload APKs to the device through the Agent (advanced).", cap: "install" },
  ],
  androidtv_remote: [
    { label: "Send keys", hint: "Full remote keys including D-pad — required for babsonnexus App Play configs.", cap: "keys" },
    { label: "D-pad / App Play", hint: "Can run ADBTuner App Play navigation scripts without ADB.", always: true },
    { label: "Foreground app", hint: "Reads which app is in the foreground after a tune.", cap: "current_app" },
    { label: "Playback check", hint: "Best-effort playback detection. May be limited compared to the Agent APK.", cap: "playback_state" },
  ],
  firetv_rest: [
    { label: "Send keys", hint: "D-pad and Home/Back via the Fire TV Remote HTTP API (no ADB).", cap: "keys" },
    { label: "D-pad / App Play", hint: "Can run ADBTuner App Play navigation scripts on Fire Stick / Fire TV without ADB.", always: true },
    { label: "Launch apps", hint: "Opens apps by package name through the Fire TV Remote protocol.", always: true },
  ],
  adb: [
    { label: "Send keys", hint: "Full keyevents via network ADB (including D-pad).", cap: "keys" },
    { label: "D-pad / App Play", hint: "Runs babsonnexus App Play scripts over network ADB — Fire OS 7 fallback when firetv_rest is unavailable.", always: true },
    { label: "Force-stop", hint: "Real am force-stop (closer to ADBTuner than HOME-only backends).", always: true },
    { label: "App list", hint: "Lists installed packages via pm list packages.", cap: "app_list" },
  ],
};

// ---- UI utilities ----
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }
function toast(msg, isErr, ms) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => t.classList.add("hidden"), ms || (isErr ? 8000 : 3200));
}
function openModal(title, node) {
  document.getElementById("modal-title").textContent = title;
  const body = document.getElementById("modal-body");
  body.innerHTML = ""; body.appendChild(node);
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal() {
  stopAllPreviews();
  const modalCard = document.querySelector("#modal .modal-card");
  if (modalCard) modalCard.classList.remove("modal-card-preview");
  document.getElementById("modal").classList.add("hidden");
}

// ---- Navigation ----
document.querySelectorAll(".nav-item").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    if (tab.dataset.tab === "status") startStatusPolling(); else stopStatusPolling();
    if (tab.dataset.tab === "channels") loadChannels();
    if (tab.dataset.tab === "configurations") loadConfigurations();
    if (tab.dataset.tab === "tuners") loadTuners();
    else stopAllPreviews();
    if (tab.dataset.tab === "options") loadOptions();
  });
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopAllPreviews();
});

// ---- M3U + HDHomeRun URLs ----
function initM3u() {
  const url = `${location.origin}/channels.m3u`;
  document.getElementById("m3u-url").value = url;
}
function initHdhr(status) {
  const input = document.getElementById("hdhr-url");
  const meta = document.getElementById("hdhr-meta");
  if (!input) return;
  const hdhr = status && status.hdhr;
  if (!hdhr || !hdhr.enabled) {
    input.value = "(disabled)";
    if (meta) meta.textContent = "Enable HDHomeRun in Options";
    return;
  }
  input.value = hdhr.base_url || location.origin;
  if (meta) {
    const parts = [
      `${hdhr.tuner_count} tuner${hdhr.tuner_count === 1 ? "" : "s"}`,
      hdhr.device_id ? `ID ${hdhr.device_id}` : null,
      hdhr.discovery_running ? "discovery on" : "manual IP only",
      hdhr.xmltv_url ? "XMLTV ready" : "set Channels DVR URL for EPG",
    ].filter(Boolean);
    meta.textContent = parts.join(" · ");
  }
  const xmltvInput = document.getElementById("xmltv-url");
  if (xmltvInput) {
    xmltvInput.value = hdhr.xmltv_url || `${location.origin}/xmltv.xml`;
  }
}
document.getElementById("copy-m3u").addEventListener("click", () => {
  const input = document.getElementById("m3u-url");
  navigator.clipboard.writeText(input.value).then(() => toast("M3U URL copied"));
});
document.getElementById("copy-hdhr").addEventListener("click", () => {
  const input = document.getElementById("hdhr-url");
  if (!input.value || input.value.startsWith("(")) {
    toast("HDHomeRun is disabled", true);
    return;
  }
  navigator.clipboard.writeText(input.value).then(() => toast("HDHomeRun URL copied"));
});
document.getElementById("copy-xmltv").addEventListener("click", () => {
  const input = document.getElementById("xmltv-url");
  navigator.clipboard.writeText(input.value).then(() => toast("XMLTV URL copied"));
});

// ============================ TUNERS ============================
let cachedChannels = [];
let cachedConfigs = [];
let cachedAgentLatest = null;
/** @type {null | {tuners: any[], channels: any[], summary: any}} */
let packageCoverage = null;
/** True when imported channels need D-pad (App Play, Max who’s-watching, key_macro). */
let catalogNeedsDpadKeys = false;

const AGENT_ONLY_KEYS = new Set(["BACK", "HOME", "RECENTS", "APP_SWITCH"]);

function urlLooksLikeDeeplink(url) {
  const u = String(url || "").trim();
  if (!u) return false;
  return u.includes("://") || u.startsWith("http:") || u.startsWith("https:");
}

function expandKeyMacroTokens(value) {
  if (value == null) return [];
  const parts = Array.isArray(value) ? value : [value];
  const tokens = [];
  for (const part of parts) {
    if (part == null) continue;
    for (const piece of String(part).replace(/;/g, ",").split(",")) {
      let key = piece.trim();
      if (!key) continue;
      if (key.toUpperCase().startsWith("KEYCODE_")) key = key.slice(8);
      tokens.push(key.toUpperCase());
    }
  }
  return tokens;
}

function channelNeedsDpad(ch, configByUuid) {
  const uuid = String(ch.configuration_uuid || "").trim();
  // App Play: configuration + non-deeplink identifier (loop index).
  if (uuid && !urlLooksLikeDeeplink(ch.url)) return true;
  const keys = expandKeyMacroTokens(ch.key_macro);
  if (keys.some((k) => !AGENT_ONLY_KEYS.has(k))) return true;
  if (uuid && configByUuid[uuid]) {
    const opts = configByUuid[uuid].global_options || {};
    // Server default is true when the flag is present on Max / Compatibility configs.
    if (opts.check_for_and_clear_whos_watching_prompts) return true;
  }
  return false;
}

function computeCatalogNeedsDpad(channels, configs) {
  const byUuid = {};
  for (const cfg of configs || []) {
    if (cfg && cfg.uuid) byUuid[cfg.uuid] = cfg;
  }
  return (channels || []).some((ch) => channelNeedsDpad(ch, byUuid));
}

function renderKeysControlWarning(tuners, needsDpad) {
  const banner = document.getElementById("keys-control-warning");
  if (!banner) return;
  const missing = (tuners || []).filter(
    (t) =>
      t.enabled !== false
      && t.control
      && t.control.type === "http_agent"
      && !(t.keys_control && t.keys_control.type)
  );
  if (!needsDpad || !missing.length) {
    banner.classList.add("hidden");
    banner.innerHTML = "";
    return;
  }
  const names = missing.map((t) => t.name).join(", ");
  banner.classList.remove("hidden");
  banner.innerHTML = `
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/></svg>
    <div>
      <strong>Keys / D-pad backend missing</strong> on ${escapeHtml(names)}.
      Max profile prompts, App Play (ESPN, etc.), and DPAD key macros need
      <code>androidtv_remote</code> (Google TV / onn), <code>firetv_rest</code>, or <code>adb</code> (Fire).
      Edit each Agent tuner → set <b>Keys / D-pad backend</b> → <b>Pair</b>.
      Without it, those tunes fail instead of opening a stuck profile or home screen.
    </div>`;
}

async function fetchAgentLatest() {
  try {
    cachedAgentLatest = await api.get("/api/agent/latest");
  } catch (_) {
    cachedAgentLatest = null;
  }
  return cachedAgentLatest;
}

async function loadTuners() {
  const list = document.getElementById("tuner-list");
  stopAllPreviews();
  let tuners = [];
  try {
    const [t, channels, configs] = await Promise.all([
      api.get("/api/tuners"),
      api.get("/api/channels").catch(() => cachedChannels || []),
      api.get("/api/configurations").catch(() => cachedConfigs || []),
    ]);
    tuners = t;
    cachedChannels = channels;
    cachedConfigs = configs;
    catalogNeedsDpadKeys = computeCatalogNeedsDpad(channels, configs);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const latestPromise = fetchAgentLatest();
  renderKeysControlWarning(tuners, catalogNeedsDpadKeys);
  if (!tuners.length) {
    list.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon">📡</div>
        <h3>No tuners configured</h3>
        <p>Add a Google TV device and its HDMI encoder stream to start tuning channels.</p>
        <button class="btn btn-primary" id="empty-add-tuner">Add your first tuner</button>
      </div>`;
    list.querySelector("#empty-add-tuner")?.addEventListener("click", () => tunerForm(null));
    return;
  }
  await latestPromise;
  list.innerHTML = "";
  for (const t of tuners) {
    const backendLabel =
      t.control.type === "http_agent" ? "Agent APK"
      : t.control.type === "firetv_rest" ? "Fire TV REST"
      : t.control.type === "adb" ? "Network ADB"
      : "TV Remote";
    const keysType = t.keys_control && t.keys_control.type;
    const keysLabel =
      keysType === "androidtv_remote" ? "Remote keys"
      : keysType === "firetv_rest" ? "Fire REST keys"
      : keysType === "adb" ? "ADB keys"
      : null;
    const isAgent = t.control.type === "http_agent";
    // Fire setup: grant Agent special-access via network ADB (Agent primary, or
    // Network ADB tuner that still has the Agent APK installed).
    const showGrantPerms = isAgent || t.control.type === "adb";
    const needsKeysWarn = isAgent && !keysType && catalogNeedsDpadKeys;
    const needsPair =
      t.control.type === "androidtv_remote" || t.control.type === "firetv_rest"
      || keysType === "androidtv_remote" || keysType === "firetv_rest";
    const card = el(`<article class="card"></article>`);
    card.innerHTML = `
      <div class="card-head">
        <div>
          <div class="card-title">${escapeHtml(t.name)}</div>
          <div class="card-sub">
            <span class="backend-pill">${backendLabel}</span>
            ${keysLabel ? `&nbsp;<span class="backend-pill">${keysLabel}</span>` : ""}
            &nbsp;·&nbsp; ${escapeHtml(t.control.host)}${t.control.port ? ":" + t.control.port : ""}
          </div>
        </div>
        <div class="card-badges">
          <span class="badge ${t.enabled ? "on" : "off"}">${t.enabled ? "Enabled" : "Disabled"}</span>
          <span class="badge muted" data-health title="Checking whether the device is reachable…">Checking…</span>
          ${isAgent ? `<span class="badge muted" data-agent-version title="Installed Agent APK version">Agent …</span>` : ""}
          ${needsKeysWarn ? `<span class="badge warn" title="Edit this tuner and set Keys / D-pad backend, then Pair">No D-pad keys</span>` : ""}
        </div>
      </div>
      ${needsKeysWarn ? `<div class="card-callout-warn">Max / App Play / DPAD macros need a Keys / D-pad backend on this Agent tuner. Edit → set <b>androidtv_remote</b> (or Fire <b>firetv_rest</b> / <b>adb</b>) → Pair.</div>` : ""}
      <div class="card-meta">
        <div class="card-row"><span class="label">Encoder</span><span class="value mono">${escapeHtml(t.stream_endpoint)}</span></div>
      </div>
      <div class="cap-section">
        <div class="cap-label">What this backend can do</div>
        <div class="badges" data-badges></div>
      </div>
      <div class="card-actions">
        <button class="btn btn-sm btn-primary" data-act="preview" title="Open encoder stream preview with remote controls">Preview</button>
        <button class="btn btn-sm btn-secondary" data-act="health" title="Ping the device to verify the Agent APK or TV remote is reachable on the network">Recheck connection</button>
        ${showGrantPerms ? `<button class="btn btn-sm btn-secondary" data-act="grant-perms" title="Fire TV one-time setup: grant overlay/usage/notification via network ADB. Day-to-day tuning stays on the Agent HTTP API when using http_agent.">Grant permissions (ADB)</button>` : ""}
        ${isAgent ? `<button class="btn btn-sm btn-secondary hidden" data-act="update-agent" title="Download the latest Agent APK and open the Install dialog on the TV">Update Agent</button>` : ""}
        ${needsPair ? `<button class="btn btn-sm btn-secondary" data-act="pair">Pair</button><span data-pair-status class="badge muted">…</span>` : ""}
        <button class="btn btn-sm btn-ghost" data-act="edit">Edit</button>
        <button class="btn btn-sm btn-danger" data-act="delete">Delete</button>
      </div>`;
    const badges = card.querySelector("[data-badges]");
    renderCapabilityBadges(badges, t.control.type, keysType);
    const healthBtn = card.querySelector('[data-act="health"]');
    const healthBadge = card.querySelector("[data-health]");
    const versionBadge = card.querySelector("[data-agent-version]");
    const updateBtn = card.querySelector('[data-act="update-agent"]');
    const grantBtn = card.querySelector('[data-act="grant-perms"]');
    const setHealth = (online) => {
      healthBadge.className = `badge ${online ? "on" : "off"}`;
      healthBadge.textContent = online ? "Reachable" : "Unreachable";
      healthBadge.title = online
        ? "Device responded to a health check"
        : "Device did not respond — check IP, Agent APK, or network";
      card.classList.toggle("card-online", online);
      card.classList.toggle("card-offline", !online);
    };
    const applyAgentVersion = (info) => {
      if (!versionBadge) return;
      const code = info && info.version_code != null ? Number(info.version_code) : null;
      const name = info && info.version_name ? String(info.version_name) : null;
      if (name == null && code == null) {
        versionBadge.className = "badge muted";
        versionBadge.textContent = "Agent ?";
        versionBadge.title = "Agent did not report a version";
        if (updateBtn) updateBtn.classList.add("hidden");
        return;
      }
      const latest = cachedAgentLatest;
      const latestCode = latest && latest.versionCode != null ? Number(latest.versionCode) : null;
      const outdated = latestCode != null && code != null && code < latestCode;
      versionBadge.className = `badge ${outdated ? "warn" : "on"}`;
      versionBadge.textContent = outdated
        ? `Update available (${name || code})`
        : `Agent v${name || code}`;
      versionBadge.title = outdated
        ? `Installed ${name || "?"} (${code}); latest is ${latest.versionName} (${latestCode})`
        : `Installed Agent version ${name || "?"} (${code})`;
      if (updateBtn) updateBtn.classList.toggle("hidden", !outdated);
    };
    const runHealthCheck = async () => {
      healthBtn.disabled = true;
      healthBtn.textContent = "Checking…";
      healthBadge.className = "badge muted";
      healthBadge.textContent = "Checking…";
      try {
        const r = await api.get(`/api/tuners/${t.id}/health`);
        setHealth(r.online);
        if (r.online) {
          await refreshCapabilityStatus(badges, t);
          if (isAgent) {
            try {
              const info = await api.get(`/api/tuners/${t.id}/info`);
              applyAgentVersion(info);
            } catch (_) {
              applyAgentVersion(null);
            }
          }
        } else if (versionBadge) {
          versionBadge.className = "badge muted";
          versionBadge.textContent = "Agent …";
          if (updateBtn) updateBtn.classList.add("hidden");
        }
      } catch (err) {
        setHealth(false);
        toast(err.message, true);
      }
      healthBtn.disabled = false;
      healthBtn.textContent = "Recheck connection";
    };
    healthBtn.addEventListener("click", runHealthCheck);
    if (grantBtn) {
      grantBtn.addEventListener("click", async () => {
        const proceed = window.confirm(
          "One-time Fire TV / Fire Stick setup via network ADB.\n\n" +
            "Requires ADB debugging on the device (and an accepted RSA prompt). " +
            "Appends Agent notification/accessibility bindings without removing other apps. " +
            "Day-to-day tuning stays on the Agent HTTP API (no ADB).\n\nContinue?",
        );
        if (!proceed) return;
        grantBtn.disabled = true;
        grantBtn.textContent = "Granting…";
        try {
          const r = await api.post(`/api/tuners/${t.id}/grant-permissions`, {});
          const tail = Array.isArray(r.messages) && r.messages.length
            ? " — " + r.messages.slice(-3).join("; ")
            : "";
          if (r.success) {
            toast((r.message || "Permissions granted") + tail, false, 6000);
          } else {
            toast((r.message || "Partial grant — check device ADB") + tail, true, 10000);
          }
          await runHealthCheck();
        } catch (err) {
          toast(err.message, true, 10000);
        }
        grantBtn.disabled = false;
        grantBtn.textContent = "Grant permissions (ADB)";
      });
    }
    if (updateBtn) {
      updateBtn.addEventListener("click", async () => {
        updateBtn.disabled = true;
        updateBtn.textContent = "Updating…";
        try {
          const r = await api.post(`/api/tuners/${t.id}/update-agent`, {});
          toast(r.message || (r.updated
            ? "Install dialog opened on the TV — confirm with the remote"
            : "Agent already up to date"));
          await runHealthCheck();
        } catch (err) {
          toast(err.message, true);
        }
        updateBtn.disabled = false;
        updateBtn.textContent = "Update Agent";
      });
    }
    const pairBtn = card.querySelector('[data-act="pair"]');
    if (pairBtn) {
      pairBtn.addEventListener("click", () => pairFlow(t));
      refreshPairStatus(t, card.querySelector("[data-pair-status]"));
    }
    card.querySelector('[data-act="edit"]').addEventListener("click", () => tunerForm(t));
    card.querySelector('[data-act="delete"]').addEventListener("click", async () => {
      if (!confirm(`Delete tuner "${t.name}"?`)) return;
      try { await api.del(`/api/tuners/${t.id}`); toast("Tuner deleted"); loadTuners(); }
      catch (err) { toast(err.message, true); }
    });
    card.querySelector('[data-act="preview"]')?.addEventListener("click", () => openTunerPreview(t));
    list.appendChild(card);
    runHealthCheck();
  }
}

/** Cleanup for the open preview modal stream. */
let _previewModalStop = null;

function stopAllPreviews() {
  if (typeof _previewModalStop === "function") {
    try { _previewModalStop(); } catch (_) { /* ignore */ }
    _previewModalStop = null;
  }
}

function openTunerPreview(tuner) {
  stopAllPreviews();
  if (!(tuner.stream_endpoint || "").trim()) {
    toast("This tuner has no encoder stream URL", true);
    return;
  }

  const node = el(`<div class="preview-modal"></div>`);
  node.innerHTML = `
    <div class="preview-stage">
      <img class="preview-stream" alt="Encoder preview" decoding="async" />
      <div class="preview-overlay">Connecting to encoder…</div>
    </div>
    <div class="preview-remote" role="group" aria-label="Remote controls">
      <div class="preview-remote-row">
        <button type="button" class="preview-key" data-key="DPAD_UP" title="Up">↑</button>
        <button type="button" class="preview-key" data-key="DPAD_DOWN" title="Down">↓</button>
        <button type="button" class="preview-key" data-key="DPAD_LEFT" title="Left">←</button>
        <button type="button" class="preview-key" data-key="DPAD_RIGHT" title="Right">→</button>
        <button type="button" class="preview-key preview-key-wide" data-key="DPAD_CENTER" title="Select / Enter">Enter</button>
        <button type="button" class="preview-key preview-key-wide" data-key="BACK" title="Back">Back</button>
        <button type="button" class="preview-key preview-key-wide" data-key="HOME" title="Home">Home</button>
      </div>
      <div class="preview-remote-row">
        <button type="button" class="preview-key preview-key-wide" data-key="VOLUME_UP" title="Volume up">Vol. Up</button>
        <button type="button" class="preview-key preview-key-wide" data-key="VOLUME_DOWN" title="Volume down">Vol. Down</button>
        <button type="button" class="preview-key preview-key-wide" data-key="WAKE" title="Wake device">Wake</button>
        <button type="button" class="preview-key preview-key-wide" data-key="SLEEP" title="Sleep / standby">Sleep</button>
        <button type="button" class="preview-key preview-key-wide" data-key="REBOOT" title="Reboot (ADB only)">Reboot</button>
      </div>
      <p class="preview-remote-hint muted">Arrows / Enter need <b>Keys / D-pad</b> set on the tuner (androidtv_remote, firetv_rest, or adb) and Pair. Agent alone: Back / Home only.</p>
    </div>`;

  const img = node.querySelector(".preview-stream");
  const overlay = node.querySelector(".preview-overlay");
  let pollTimer = null;
  let alive = true;

  const stop = () => {
    alive = false;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (img) {
      img.onload = null;
      img.onerror = null;
      img.removeAttribute("src");
    }
  };
  _previewModalStop = stop;

  const markLive = (label) => {
    if (!alive) return;
    if (overlay) {
      overlay.textContent = label || "";
      overlay.classList.add("hidden");
    }
  };
  const markStatus = (text, isErr) => {
    if (!overlay) return;
    overlay.classList.remove("hidden");
    overlay.textContent = text;
    overlay.classList.toggle("is-error", !!isErr);
  };

  const startJpegPoll = () => {
    if (!alive) return;
    markStatus("Snapshot preview…");
    const tick = () => {
      if (!alive || document.hidden) return;
      img.src = `/api/tuners/${encodeURIComponent(tuner.id)}/preview.jpg?t=${Date.now()}`;
    };
    img.onload = () => markLive("Live");
    img.onerror = () => markStatus("Preview unavailable — check encoder URL / ffmpeg", true);
    tick();
    pollTimer = setInterval(tick, 1500);
  };

  const startMjpeg = () => {
    markStatus("Connecting…");
    let settled = false;
    const failTimer = setTimeout(() => {
      if (settled || !alive) return;
      img.removeAttribute("src");
      startJpegPoll();
    }, 6000);
    img.onload = () => {
      if (!alive) return;
      settled = true;
      clearTimeout(failTimer);
      markLive();
    };
    img.onerror = () => {
      if (!alive) return;
      settled = true;
      clearTimeout(failTimer);
      img.removeAttribute("src");
      startJpegPoll();
    };
    img.src = `/api/tuners/${encodeURIComponent(tuner.id)}/preview.mjpg?t=${Date.now()}`;
  };

  node.querySelectorAll("[data-key]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.getAttribute("data-key");
      if (!key) return;
      btn.disabled = true;
      try {
        await api.post(`/api/tuners/${encodeURIComponent(tuner.id)}/key`, { key });
      } catch (e) {
        toast(e.message, true);
      }
      btn.disabled = false;
    });
  });

  // Widen modal for video + pad.
  const modalCard = document.querySelector("#modal .modal-card");
  if (modalCard) modalCard.classList.add("modal-card-preview");
  openModal(tuner.name, node);
  startMjpeg();
}

function renderCapabilityBadges(container, backendType, keysType) {
  container.innerHTML = "";
  const defs = [...(CAP_DEFS[backendType] || [])];
  if (keysType && keysType !== backendType) {
    const keyDefs = CAP_DEFS[keysType] || [];
    keyDefs.forEach((def) => {
      if (def.label.includes("D-pad") || def.label === "Force-stop") {
        defs.push({ ...def, label: def.label + " (keys)", hint: def.hint + " Via keys_control." });
      }
    });
  }
  defs.forEach((def) => {
    const badge = el(`<span class="badge cap-badge accent" title="${escapeAttr(def.hint)}">${escapeHtml(def.label)}</span>`);
    if (def.cap) badge.dataset.cap = def.cap;
    if (def.always) badge.dataset.always = "1";
    container.appendChild(badge);
  });
}

async function refreshCapabilityStatus(container, tuner) {
  const badges = [...container.querySelectorAll("[data-cap]")];
  if (!badges.length) return;
  try {
    const info = await api.get(`/api/tuners/${tuner.id}/info`);
    const caps = info.capabilities || {};
    badges.forEach((badge) => {
      const key = badge.dataset.cap;
      const on = !!caps[key];
      badge.classList.remove("accent", "on", "off", "muted");
      badge.classList.add("cap-badge", on ? "on" : "off");
      const baseHint = badge.getAttribute("title") || "";
      const status = on ? "Active on this device." : "Not available — grant the permission on the device or check the Agent app.";
      badge.setAttribute("title", `${baseHint} ${status}`);
    });
  } catch {
    badges.forEach((badge) => {
      badge.classList.remove("on", "off");
      badge.classList.add("accent", "muted");
    });
  }
}

async function refreshPairStatus(tuner, badge) {
  if (!badge) return;
  try {
    const r = await api.get(`/api/tuners/${tuner.id}/pair/status`);
    if (!r.requires_pairing) {
      badge.className = "badge";
      badge.textContent = "n/a";
      return;
    }
    badge.className = `badge ${r.paired ? "on" : "off"}`;
    badge.textContent = r.paired ? "Paired" : "Not paired";
  } catch (e) {
    badge.className = "badge off";
    badge.textContent = "pair unknown";
  }
}

function tunerForm(existing) {
  const t = existing || {
    name: "",
    control: { type: "http_agent", host: "", port: 9092, pair_port: null, token: "" },
    keys_control: null,
    stream_endpoint: "",
    enabled: true,
  };
  const kc = t.keys_control || { type: "", host: "", port: null, pair_port: null, token: "" };
  const form = el(`<form class="form-grid"></form>`);
  form.innerHTML = `
    <div class="field full"><label>Name</label><input name="name" value="${escapeAttr(t.name)}" required /></div>
    <div class="field"><label>Backend</label>
      <select name="type">
        <option value="http_agent">http_agent (Agent APK) — recommended for deep links</option>
        <option value="androidtv_remote">androidtv_remote (Google TV Remote) — App Play</option>
        <option value="firetv_rest">firetv_rest (Fire TV Remote HTTP) — App Play on Fire</option>
        <option value="adb">adb (network ADB) — Fire App Play fallback</option>
      </select>
    </div>
    <div class="field"><label>Host / IP</label><input name="host" value="${escapeAttr(t.control.host)}" required /></div>
    <div class="field"><label>Port <span class="hint">(updates with backend; blank = default)</span></label><input name="port" type="number" value="${t.control.port ?? ""}" /></div>
    <div class="field" data-remote><label>Pair port <span class="hint">(Google TV remote, default 6467)</span></label><input name="pair_port" type="number" value="${t.control.pair_port ?? ""}" /></div>
    <div class="field" data-token><label>Token <span class="hint" data-token-hint>(agent auth or Fire TV client token)</span></label><input name="token" value="${escapeAttr(t.control.token || "")}" /></div>
    <div class="field full" data-keys-section>
      <label>Keys / D-pad backend <span class="hint">(optional hybrid — keep Agent for YTTV launches; add Remote or ADB for Max profile / App Play)</span></label>
      <select name="keys_type">
        <option value="">None</option>
        <option value="androidtv_remote">androidtv_remote (Google TV)</option>
        <option value="firetv_rest">firetv_rest (Fire)</option>
        <option value="adb">adb (network ADB)</option>
      </select>
      <p class="field-warn ${catalogNeedsDpadKeys && !(kc.type) ? "" : "hidden"}" data-keys-warn>
        Your channel list includes Max / App Play / DPAD macros. Set a Keys / D-pad backend here, save, then use <b>Pair</b> on the tuner card — otherwise those tunes will fail.
      </p>
    </div>
    <div class="field" data-keys-host><label>Keys host <span class="hint">(blank = same as primary)</span></label><input name="keys_host" value="${escapeAttr(kc.host || "")}" /></div>
    <div class="field" data-keys-port><label>Keys port</label><input name="keys_port" type="number" value="${kc.port ?? ""}" placeholder="6466 / 8080 / 5555" /></div>
    <div class="field" data-keys-pair><label>Keys pair port</label><input name="keys_pair_port" type="number" value="${kc.pair_port ?? ""}" placeholder="6467" /></div>
    <div class="field" data-keys-token><label>Keys token <span class="hint">(Fire REST)</span></label><input name="keys_token" value="${escapeAttr(kc.token || "")}" /></div>
    <div class="field full"><label>Encoder stream URL <span class="hint">(HDMI encoder MPEG-TS)</span></label><input name="stream_endpoint" value="${escapeAttr(t.stream_endpoint)}" placeholder="http://192.0.2.20:8090/stream0" required /></div>
    <div class="field checkbox full"><input type="checkbox" name="enabled" ${t.enabled ? "checked" : ""} /><label>Enabled</label></div>
    <div class="form-actions full"><button type="button" class="btn btn-ghost" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>`;
  const DEFAULT_PORTS = {
    http_agent: 9092,
    androidtv_remote: 6466,
    firetv_rest: 8080,
    adb: 5555,
  };
  const DEFAULT_KEYS_PORTS = {
    androidtv_remote: 6466,
    firetv_rest: 8080,
    adb: 5555,
  };
  const DEFAULT_PAIR_PORT = 6467;
  const typeSel = form.querySelector('[name="type"]');
  typeSel.value = t.control.type;
  const keysTypeSel = form.querySelector('[name="keys_type"]');
  keysTypeSel.value = kc.type || "";
  let prevType = typeSel.value;
  let prevKeysType = keysTypeSel.value;
  const portInput = form.querySelector('[name="port"]');
  const keysPortInput = form.querySelector('[name="keys_port"]');
  const pairPortInput = form.querySelector('[name="pair_port"]');
  const keysPairPortInput = form.querySelector('[name="keys_pair_port"]');

  const applyDefaultPort = (selectEl, inputEl, defaults, prev) => {
    const next = selectEl.value;
    const nextDefault = defaults[next];
    if (nextDefault == null) {
      // Keys type cleared — leave whatever was typed.
      return next;
    }
    const cur = (inputEl.value || "").trim();
    const prevDefault = defaults[prev];
    const curNum = cur === "" ? null : Number(cur);
    // Blank, or still on the previous backend's default → swap to the new default.
    // Custom ports are left alone.
    if (cur === "" || (prevDefault != null && curNum === prevDefault)) {
      inputEl.value = String(nextDefault);
    }
    inputEl.placeholder = String(nextDefault);
    return next;
  };

  const applyPairPortDefault = (inputEl, isRemote) => {
    if (!inputEl) return;
    const cur = (inputEl.value || "").trim();
    if (isRemote) {
      if (cur === "" || Number(cur) === DEFAULT_PAIR_PORT) {
        inputEl.value = String(DEFAULT_PAIR_PORT);
      }
      inputEl.placeholder = String(DEFAULT_PAIR_PORT);
    } else if (cur === "" || Number(cur) === DEFAULT_PAIR_PORT) {
      // Leaving remote — clear the stock default so it isn't saved on Agent/ADB.
      inputEl.value = "";
    }
  };

  const syncType = () => {
    const type = typeSel.value;
    form.querySelector("[data-remote]").style.display = type === "androidtv_remote" ? "" : "none";
    form.querySelector("[data-token]").style.display = (type === "http_agent" || type === "firetv_rest") ? "" : "none";
    const hint = form.querySelector("[data-token-hint]");
    if (hint) {
      hint.textContent = type === "firetv_rest"
        ? "(filled automatically after Pair; optional)"
        : "(agent, optional)";
    }
    prevType = applyDefaultPort(typeSel, portInput, DEFAULT_PORTS, prevType);
    applyPairPortDefault(pairPortInput, type === "androidtv_remote");
    // Hybrid keys section is most useful when primary is Agent.
    form.querySelector("[data-keys-section]").style.display =
      type === "http_agent" || keysTypeSel.value ? "" : "none";
    syncKeys();
  };
  const syncKeys = () => {
    const kt = keysTypeSel.value;
    const show = !!kt;
    form.querySelector("[data-keys-host]").style.display = show ? "" : "none";
    form.querySelector("[data-keys-port]").style.display = show ? "" : "none";
    form.querySelector("[data-keys-pair]").style.display = kt === "androidtv_remote" ? "" : "none";
    form.querySelector("[data-keys-token]").style.display = kt === "firetv_rest" ? "" : "none";
    if (show) {
      prevKeysType = applyDefaultPort(
        keysTypeSel,
        keysPortInput,
        DEFAULT_KEYS_PORTS,
        prevKeysType,
      );
    } else {
      prevKeysType = "";
    }
    applyPairPortDefault(keysPairPortInput, kt === "androidtv_remote");
    const warn = form.querySelector("[data-keys-warn]");
    if (warn) {
      const showWarn = catalogNeedsDpadKeys && typeSel.value === "http_agent" && !kt;
      warn.classList.toggle("hidden", !showWarn);
    }
  };
  typeSel.addEventListener("change", syncType);
  keysTypeSel.addEventListener("change", syncKeys);
  // Initial sync: show/hide fields only — do not overwrite saved ports on edit.
  const type = typeSel.value;
  form.querySelector("[data-remote]").style.display = type === "androidtv_remote" ? "" : "none";
  form.querySelector("[data-token]").style.display = (type === "http_agent" || type === "firetv_rest") ? "" : "none";
  const hint = form.querySelector("[data-token-hint]");
  if (hint) {
    hint.textContent = type === "firetv_rest"
      ? "(filled automatically after Pair; optional)"
      : "(agent, optional)";
  }
  portInput.placeholder = String(DEFAULT_PORTS[type] || "");
  if (type === "androidtv_remote" && !(pairPortInput.value || "").trim()) {
    pairPortInput.placeholder = String(DEFAULT_PAIR_PORT);
  }
  form.querySelector("[data-keys-section]").style.display =
    type === "http_agent" || keysTypeSel.value ? "" : "none";
  {
    const kt = keysTypeSel.value;
    const show = !!kt;
    form.querySelector("[data-keys-host]").style.display = show ? "" : "none";
    form.querySelector("[data-keys-port]").style.display = show ? "" : "none";
    form.querySelector("[data-keys-pair]").style.display = kt === "androidtv_remote" ? "" : "none";
    form.querySelector("[data-keys-token]").style.display = kt === "firetv_rest" ? "" : "none";
    if (show && DEFAULT_KEYS_PORTS[kt] != null) {
      keysPortInput.placeholder = String(DEFAULT_KEYS_PORTS[kt]);
    }
    if (kt === "androidtv_remote" && !(keysPairPortInput.value || "").trim()) {
      keysPairPortInput.placeholder = String(DEFAULT_PAIR_PORT);
    }
    const warn = form.querySelector("[data-keys-warn]");
    if (warn) {
      const showWarn = catalogNeedsDpadKeys && type === "http_agent" && !kt;
      warn.classList.toggle("hidden", !showWarn);
    }
  }
  // New tuner with empty port: seed defaults once.
  if (!existing || !existing.id) {
    if (!(portInput.value || "").trim() && DEFAULT_PORTS[type] != null) {
      portInput.value = String(DEFAULT_PORTS[type]);
    }
    if (type === "androidtv_remote" && !(pairPortInput.value || "").trim()) {
      pairPortInput.value = String(DEFAULT_PAIR_PORT);
    }
  }
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const keysType = (fd.get("keys_type") || "").toString();
    let keys_control = null;
    if (keysType) {
      keys_control = {
        type: keysType,
        host: (fd.get("keys_host") || "").toString().trim() || (fd.get("host") || "").toString(),
        port: fd.get("keys_port") ? Number(fd.get("keys_port")) : null,
        pair_port: fd.get("keys_pair_port") ? Number(fd.get("keys_pair_port")) : null,
        token: fd.get("keys_token") || null,
      };
    }
    const payload = {
      name: fd.get("name"),
      stream_endpoint: fd.get("stream_endpoint"),
      enabled: form.querySelector('[name="enabled"]').checked,
      control: {
        type: fd.get("type"),
        host: fd.get("host"),
        port: fd.get("port") ? Number(fd.get("port")) : null,
        pair_port: fd.get("pair_port") ? Number(fd.get("pair_port")) : null,
        token: fd.get("token") || null,
      },
      keys_control,
    };
    try {
      // Discover-prefilled forms pass a seed object without an id — always create those.
      if (existing && existing.id) await api.put(`/api/tuners/${existing.id}`, { id: existing.id, ...payload });
      else await api.post("/api/tuners", payload);
      toast("Tuner saved"); closeModal(); loadTuners();
    } catch (err) { toast(err.message, true); }
  });
  openModal(existing && existing.id ? "Edit tuner" : "Add tuner", form);
}
document.getElementById("add-tuner-btn").addEventListener("click", () => tunerForm(null));

async function pairFlow(t) {
  const pairType = (t.keys_control && (t.keys_control.type === "androidtv_remote" || t.keys_control.type === "firetv_rest"))
    ? t.keys_control.type
    : t.control.type;
  const isFire = pairType === "firetv_rest";
  const hasStream = !!(t.stream_endpoint && String(t.stream_endpoint).trim());
  const node = el(`<div>
    <p class="muted">Pair <b>${escapeHtml(t.name)}</b>. A PIN will appear on the TV screen.${isFire ? " Uses the Fire TV Remote HTTP API (no ADB)." : ""}${t.keys_control ? " (keys / D-pad backend)" : ""}</p>
    <p class="muted">Auto-pair reads the PIN from the HDMI encoder feed via OCR.${hasStream ? "" : " <b>Add a stream endpoint first</b> to enable Auto-pair."}</p>
    <div class="field full"><label>PIN from TV</label><input id="pair-pin" placeholder="${isFire ? "e.g. 1234" : "e.g. A1B2C3"}" /></div>
    <div class="form-actions">
      <button class="btn btn-ghost" data-cancel>Cancel</button>
      <button class="btn btn-secondary" data-auto ${hasStream ? "" : "disabled"} title="${hasStream ? "Start pairing and OCR the PIN from the encoder" : "Requires stream endpoint"}">Auto-pair</button>
      <button class="btn btn-primary" data-finish>Complete pairing</button>
    </div>
    <p id="pair-msg" class="muted"></p>
  </div>`);
  node.querySelector("[data-cancel]").addEventListener("click", closeModal);
  const msg = node.querySelector("#pair-msg");
  const pinInput = node.querySelector("#pair-pin");
  let pairingStarted = false;
  openModal(isFire ? "Pair Fire TV" : "Pair Android TV", node);
  // Show the PIN on the TV immediately so manual entry works without Auto-pair.
  // Auto-pair must OCR+finish that same session — restarting orphans the PIN.
  const startPromise = (async () => {
    try {
      await api.post(`/api/tuners/${t.id}/pair/start`);
      pairingStarted = true;
      msg.textContent = hasStream
        ? "PIN should be on the TV — Auto-pair, or type it and Complete pairing."
        : "PIN should be on the TV — enter it below and Complete pairing.";
      return true;
    } catch (e) {
      msg.textContent = "Failed to start pairing: " + e.message;
      return false;
    }
  })();

  node.querySelector("[data-auto]").addEventListener("click", async () => {
    const btn = node.querySelector("[data-auto]");
    btn.disabled = true;
    msg.textContent = "Waiting for pairing to start…";
    try {
      await startPromise;
      msg.textContent = pairingStarted
        ? "Reading PIN from encoder…"
        : "Starting pairing and reading PIN from encoder…";
      // If the modal already started pairing, skip start_pairing so androidtv_remote
      // keeps the remote session whose PIN is on screen.
      const r = await api.post(`/api/tuners/${t.id}/pair/auto`, {
        already_started: pairingStarted,
      });
      if (r.pairing_started) pairingStarted = true;
      if (r.success) {
        toast("Paired successfully");
        closeModal();
        loadTuners();
        return;
      }
      if (r.pin) pinInput.value = r.pin;
      msg.textContent = r.hint || "Couldn't read the PIN — enter it manually, then Complete pairing.";
    } catch (e) {
      msg.textContent = "Auto-pair failed: " + e.message + " Enter the PIN shown on the TV, then Complete pairing.";
    } finally {
      btn.disabled = !hasStream;
    }
  });

  node.querySelector("[data-finish]").addEventListener("click", async () => {
    const pin = pinInput.value.trim();
    if (!pin) { msg.textContent = "Please enter the PIN."; return; }
    try {
      if (!pairingStarted) {
        await api.post(`/api/tuners/${t.id}/pair/start`);
        pairingStarted = true;
      }
      await api.post(`/api/tuners/${t.id}/pair/finish`, { pin });
      toast("Paired successfully");
      closeModal();
      loadTuners();
    } catch (e) {
      msg.textContent = "Pairing failed: " + e.message;
    }
  });
}

// ---- Discover ----
document.getElementById("discover-btn").addEventListener("click", async () => {
  const box = document.getElementById("discovered");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="muted">Scanning the network…</div>`;
  try {
    const found = await api.get("/api/discover?timeout=5");
    if (!found.length) {
      box.innerHTML = `<div class="empty">No devices found on the network. Add a tuner manually by IP.</div>`;
      return;
    }
    box.innerHTML = `<div class="card-title" style="margin-bottom:10px;font-size:14px;">Discovered devices</div>`;
    found.forEach((d) => {
      const item = el(`<div class="disc-item">
        <div><b>${escapeHtml(d.name)}</b> <span class="mono">${escapeHtml(d.host)}:${d.port}</span> <span class="badge">${d.backend}</span></div>
        <button class="btn btn-sm btn-primary">Add</button></div>`);
      item.querySelector("button").addEventListener("click", () => {
        const backend = d.backend === "androidtv_remote" ? "androidtv_remote" : "http_agent";
        const control = backend === "androidtv_remote"
          ? { type: backend, host: d.host, port: d.port || 6466, pair_port: 6467, token: "" }
          : { type: backend, host: d.host, port: d.port || 9092, pair_port: null, token: "" };
        tunerForm({ name: d.name, control, stream_endpoint: "", enabled: true });
      });
      box.appendChild(item);
    });
  } catch (e) { box.innerHTML = `<div class="muted">Discovery failed: ${escapeHtml(e.message)}</div>`; }
});

// ============================ CHANNELS ============================
function channelMatchesQuery(c, q) {
  if (!q) return true;
  const hay = `${c.number} ${c.name} ${c.provider_name || ""} ${c.package_name} ${c.url || ""}`.toLowerCase();
  return hay.includes(q);
}

function coverageForChannel(c) {
  if (!packageCoverage || !packageCoverage.channels) return null;
  return packageCoverage.channels.find((r) => Number(r.number) === Number(c.number)) || null;
}

function packageWarnTitle(row) {
  if (!row) return "";
  if (row.status === "missing") {
    return `Package not installed on: ${(row.missing_on || []).join(", ") || "Agent tuners"}`;
  }
  if (row.status === "partial") {
    return `Missing on ${(row.missing_on || []).join(", ")}; found on ${(row.found_on || []).join(", ")}`;
  }
  return "";
}

function renderChannels(channels) {
  const tbody = document.querySelector("#channel-table tbody");
  const q = (document.getElementById("channel-search")?.value || "").trim().toLowerCase();
  const filtered = channels.filter((c) => channelMatchesQuery(c, q));
  const countEl = document.getElementById("channel-count");
  const sum = packageCoverage && packageCoverage.summary;
  let countText = q
    ? `Showing ${filtered.length} of ${channels.length}`
    : `${channels.length} channel${channels.length === 1 ? "" : "s"}`;
  if (sum && (sum.channels_missing || sum.channels_partial)) {
    countText += ` · ${sum.channels_missing || 0} missing pkg · ${sum.channels_partial || 0} partial`;
  }
  if (countEl) countEl.textContent = countText;
  tbody.innerHTML = "";
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">${channels.length ? "No channels match your search." : "No channels yet — import an ADBTuner export or add one manually."}</div></td></tr>`;
    return;
  }
  for (const c of filtered) {
    const cov = coverageForChannel(c);
    const warn = cov && (cov.status === "missing" || cov.status === "partial");
    const pkgHtml = warn
      ? `<span class="mono">${escapeHtml(c.package_name)}</span> <span class="pkg-warn" title="${escapeAttr(packageWarnTitle(cov))}">⚠ ${cov.status === "missing" ? "not installed" : "partial"}</span>`
      : `<span class="mono">${escapeHtml(c.package_name)}</span>`;
    const tr = el(`<tr class="row-clickable${warn ? " row-pkg-warn" : ""}" title="Click to edit channel">
      <td><span class="ch-num">${c.number}</span></td>
      <td><strong>${escapeHtml(c.name)}</strong></td>
      <td class="muted">${escapeHtml(c.provider_name || "—")}</td>
      <td>${pkgHtml}</td>
      <td class="mono col-url" title="${escapeAttr(c.url || "")}">${escapeHtml((c.url || "—").slice(0, 48))}${(c.url || "").length > 48 ? "…" : ""}</td>
      <td class="col-actions"><button class="btn btn-sm btn-secondary" data-edit>Edit</button> <button class="btn btn-sm btn-danger" data-del>Delete</button></td>
    </tr>`);
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      channelForm(c);
    });
    tr.querySelector("[data-edit]").addEventListener("click", (e) => {
      e.stopPropagation();
      channelForm(c);
    });
    tr.querySelector("[data-del]").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete channel ${c.number}?`)) return;
      try { await api.del(`/api/channels/${c.number}`); toast("Channel deleted"); loadChannels(); }
      catch (err) { toast(err.message, true); }
    });
    tbody.appendChild(tr);
  }
}

async function refreshPackageCoverage(quiet) {
  try {
    packageCoverage = await api.get("/api/package-coverage");
    if (!quiet) {
      const s = packageCoverage.summary || {};
      if (!s.listable_tuners) {
        toast("No Agent/ADB tuners available to list apps", true);
      } else if (s.channels_missing || s.channels_partial) {
        toast(
          `Packages: ${s.channels_missing || 0} missing on all listable tuners, ${s.channels_partial || 0} partial`,
          true,
          8000
        );
      } else {
        toast(`All channel packages found on reachable Agent/ADB tuners (${s.reachable_tuners || 0})`);
      }
    }
  } catch (e) {
    packageCoverage = null;
    if (!quiet) toast(e.message, true);
  }
}

async function loadChannels() {
  try {
    cachedChannels = await api.get("/api/channels");
    catalogNeedsDpadKeys = computeCatalogNeedsDpad(cachedChannels, cachedConfigs);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  cachedChannels.sort((a, b) => a.number - b.number);
  renderChannels(cachedChannels);
  // Background package check (Agent app lists) — don't block the table.
  refreshPackageCoverage(true).then(() => renderChannels(cachedChannels));
}

document.getElementById("channel-search")?.addEventListener("input", () => {
  renderChannels(cachedChannels);
});

document.getElementById("check-packages-btn")?.addEventListener("click", async () => {
  toast("Checking installed apps on Agent tuners…");
  await refreshPackageCoverage(false);
  renderChannels(cachedChannels);
});

function updatePackageFieldWarnings(form) {
  const status = form.querySelector("[data-pkg-status]");
  if (!status) return;
  const primary = (form.querySelector('[name="package_name"]').value || "").trim();
  const alternate = (form.querySelector('[name="alternate_package_name"]').value || "").trim();
  if (!packageCoverage || !(packageCoverage.tuners || []).length) {
    status.className = "pkg-status muted";
    status.textContent = "Select a tuner below to browse installed apps, or click Check packages on the Channels page.";
    return;
  }
  const tuners = packageCoverage.tuners.filter((t) => (t.packages || []).length);
  if (!tuners.length) {
    status.className = "pkg-status warn";
    status.textContent = "Could not read installed apps (is the Agent reachable? Usage Access helps package lists).";
    return;
  }
  if (!primary) {
    status.className = "pkg-status muted";
    status.textContent = "";
    return;
  }
  const candidates = new Set([primary]);
  if (alternate) candidates.add(alternate);
  // ESPN family swap (matches server).
  if (primary === "com.espn.gtv" || alternate === "com.espn.gtv") candidates.add("com.espn.score_center");
  if (primary === "com.espn.score_center" || alternate === "com.espn.score_center") candidates.add("com.espn.gtv");

  const found = [];
  const missing = [];
  for (const t of tuners) {
    const have = new Set(t.packages || []);
    if ([...candidates].some((p) => have.has(p))) found.push(t.name);
    else missing.push(t.name);
  }
  if (missing.length && !found.length) {
    status.className = "pkg-status warn";
    status.innerHTML = `<b>Not installed</b> on ${escapeHtml(missing.join(", "))}. Pick an app from the list below, or set alternate_package_name (ESPN: gtv on Fire ↔ score_center on Google TV).`;
  } else if (missing.length) {
    status.className = "pkg-status warn";
    status.textContent = `Installed on ${found.join(", ")}; missing on ${missing.join(", ")}.`;
  } else {
    status.className = "pkg-status ok";
    status.textContent = `Package found on: ${found.join(", ")}.`;
  }
}

function channelForm(existing) {
  const c = existing || { number: "", name: "", provider_name: "", package_name: "", alternate_package_name: "", component: "", url: "", action: "android.intent.action.VIEW", extra_string: "", key_macro: [], compatibility_mode: false, tvc_guide_stationid: "", configuration_uuid: "" };
  const form = el(`<form class="form-grid"></form>`);
  form.innerHTML = `
    <div class="field"><label>Channel number</label><input name="number" type="number" value="${c.number}" ${existing ? "readonly" : ""} required /></div>
    <div class="field"><label>Name</label><input name="name" value="${escapeAttr(c.name)}" required /></div>
    <div class="field"><label>Provider</label><input name="provider_name" value="${escapeAttr(c.provider_name || "")}" /></div>
    <div class="field"><label>Gracenote station id</label><input name="tvc_guide_stationid" value="${escapeAttr(c.tvc_guide_stationid || "")}" /></div>
    <div class="field"><label>Package name</label><input name="package_name" value="${escapeAttr(c.package_name)}" required autocomplete="off" /></div>
    <div class="field"><label>Alternate package</label><input name="alternate_package_name" value="${escapeAttr(c.alternate_package_name || "")}" autocomplete="off" /></div>
    <div class="field full"><div class="pkg-status muted" data-pkg-status></div></div>
    <div class="field full"><label>Deep link URL / App Play index <span class="hint">(intent data, or loop index for App Play)</span></label><input name="url" value="${escapeAttr(c.url || "")}" placeholder="https://... or 0, 1, 2…" /></div>
    <div class="field full"><label>Configuration UUID <span class="hint">(babsonnexus App Play; leave blank for deep links)</span></label><input name="configuration_uuid" value="${escapeAttr(c.configuration_uuid || "")}" placeholder="0AppPlay-1500-0000-0000-ESPN00000000" /></div>
    <div class="field"><label>Action</label><input name="action" value="${escapeAttr(c.action || "android.intent.action.VIEW")}" /></div>
    <div class="field"><label>Component <span class="hint">(agent; Android 12+)</span></label><input name="component" value="${escapeAttr(c.component || "")}" /></div>
    <div class="field full"><label>Intent extras <span class="hint">(agent; key:value,key:value)</span></label><input name="extra_string" value="${escapeAttr(c.extra_string || "")}" /></div>
    <div class="field full"><label>Key macro <span class="hint">(after launch; comma or semicolon, e.g. DPAD_CENTER;DPAD_CENTER — needs keys_control / D-pad backend)</span></label><input name="key_macro" value="${escapeAttr((c.key_macro || []).join(","))}" /></div>
    <div class="field checkbox full"><input type="checkbox" name="compatibility_mode" ${c.compatibility_mode ? "checked" : ""} /><label>Compatibility mode (stop app before launch)</label></div>
    <div class="field full"><label>Installed apps on a tuner <span class="hint">(search, then set as package or alternate)</span></label>
      <select id="app-picker-tuner"><option value="">Select a tuner…</option></select>
      <input id="app-picker-filter" class="app-picker-filter hidden" type="search" placeholder="Filter by name or package…" />
      <div id="app-picker" class="app-picker hidden"></div>
    </div>
    <div class="form-actions full"><button type="button" class="btn btn-ghost" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>`;
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  form.querySelector('[name="package_name"]').addEventListener("input", () => updatePackageFieldWarnings(form));
  form.querySelector('[name="alternate_package_name"]').addEventListener("input", () => updatePackageFieldWarnings(form));

  let loadedApps = [];
  const renderAppPicker = () => {
    const picker = form.querySelector("#app-picker");
    const q = (form.querySelector("#app-picker-filter").value || "").trim().toLowerCase();
    const apps = !q ? loadedApps : loadedApps.filter((a) => {
      const hay = `${a.name || ""} ${a.packageName || ""}`.toLowerCase();
      return hay.includes(q);
    });
    if (!apps.length) {
      picker.innerHTML = `<div class="muted">${loadedApps.length ? "No apps match the filter." : "No apps returned."}</div>`;
      return;
    }
    picker.innerHTML = "";
    apps.slice(0, 200).forEach((a) => {
      const row = el(`<div class="app-picker-row">
        <div class="app-picker-meta"><b>${escapeHtml(a.name || a.packageName)}</b> <span class="mono">${escapeHtml(a.packageName)}</span></div>
        <div class="app-picker-actions">
          <button type="button" class="btn btn-sm btn-secondary" data-as="primary">Package</button>
          <button type="button" class="btn btn-sm btn-ghost" data-as="alt">Alternate</button>
        </div>
      </div>`);
      row.querySelector('[data-as="primary"]').addEventListener("click", () => {
        form.querySelector('[name="package_name"]').value = a.packageName;
        updatePackageFieldWarnings(form);
      });
      row.querySelector('[data-as="alt"]').addEventListener("click", () => {
        form.querySelector('[name="alternate_package_name"]').value = a.packageName;
        updatePackageFieldWarnings(form);
      });
      picker.appendChild(row);
    });
  };

  populateTunerSelect(form.querySelector("#app-picker-tuner"));
  form.querySelector("#app-picker-filter").addEventListener("input", renderAppPicker);
  form.querySelector("#app-picker-tuner").addEventListener("change", async (e) => {
    const picker = form.querySelector("#app-picker");
    const filter = form.querySelector("#app-picker-filter");
    if (!e.target.value) {
      picker.classList.add("hidden");
      filter.classList.add("hidden");
      return;
    }
    picker.classList.remove("hidden");
    filter.classList.remove("hidden");
    picker.innerHTML = `<div class="muted">Loading apps…</div>`;
    try {
      loadedApps = await api.get(`/api/tuners/${e.target.value}/apps`);
      if (!Array.isArray(loadedApps)) loadedApps = [];
      loadedApps.sort((a, b) => String(a.name || a.packageName).localeCompare(String(b.name || b.packageName)));
      renderAppPicker();
    } catch (err) {
      loadedApps = [];
      picker.innerHTML = `<div class="muted">Could not load apps: ${escapeHtml(err.message)}</div>`;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const km = (fd.get("key_macro") || "").toString().split(/[,;]/).map((s) => s.trim()).filter(Boolean);
    const payload = {
      number: Number(fd.get("number")),
      name: fd.get("name"),
      provider_name: fd.get("provider_name") || null,
      package_name: fd.get("package_name"),
      alternate_package_name: fd.get("alternate_package_name") || null,
      component: fd.get("component") || null,
      url: fd.get("url") || "",
      action: fd.get("action") || "android.intent.action.VIEW",
      extra_string: fd.get("extra_string") || null,
      key_macro: km.length ? km : null,
      compatibility_mode: form.querySelector('[name="compatibility_mode"]').checked,
      tvc_guide_stationid: fd.get("tvc_guide_stationid") || null,
      configuration_uuid: fd.get("configuration_uuid") || null,
    };
    updatePackageFieldWarnings(form);
    const status = form.querySelector("[data-pkg-status]");
    if (status && status.classList.contains("warn")) {
      if (!confirm("This package looks missing on one or more Agent tuners. Save anyway?")) return;
    }
    try {
      if (existing) await api.put(`/api/channels/${existing.number}`, payload);
      else await api.post("/api/channels", payload);
      toast("Channel saved"); closeModal(); loadChannels();
    } catch (err) { toast(err.message, true); }
  });
  openModal(existing ? `Edit channel ${existing.number}` : "Add channel", form);
  if (!packageCoverage) refreshPackageCoverage(true).then(() => updatePackageFieldWarnings(form));
  else updatePackageFieldWarnings(form);
}
document.getElementById("add-channel-btn").addEventListener("click", () => channelForm(null));

async function populateTunerSelect(sel) {
  try {
    const tuners = await api.get("/api/tuners");
    tuners.forEach((t) => sel.appendChild(el(`<option value="${t.id}">${escapeHtml(t.name)}</option>`)));
  } catch { /* ignore */ }
}

// Import / Export
document.getElementById("export-btn").addEventListener("click", async () => {
  try {
    const data = await api.get("/api/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "apituner-channels.json"; a.click();
  } catch (e) { toast(e.message, true); }
});
document.getElementById("import-btn").addEventListener("click", () => {
  const node = el(`<div>
    <p class="muted">Paste an ADBTuner (or APITuner) channel-list JSON array. Channel numbers must be unique; null numbers are filled from <code>sort_order</code> when present. App Play stations keep <code>configuration_uuid</code> — import matching configurations first.</p>
    <div class="field full"><textarea id="import-json" rows="10" placeholder="[ { &quot;number&quot;: 9000, ... } ]"></textarea></div>
    <div class="field checkbox"><input type="checkbox" id="import-replace" /><label>Replace all existing channels</label></div>
    <div class="form-actions"><button class="btn btn-ghost" data-cancel>Cancel</button><button class="btn btn-primary" data-import>Import</button></div>
  </div>`);
  node.querySelector("[data-cancel]").addEventListener("click", closeModal);
  node.querySelector("[data-import]").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(node.querySelector("#import-json").value); }
    catch { toast("Invalid JSON", true); return; }
    // Accept a bare array or an object wrapping { channels: [...] }.
    if (parsed && !Array.isArray(parsed) && Array.isArray(parsed.channels)) {
      parsed = parsed.channels;
    }
    try {
      const r = await api.post("/api/import", { channels: parsed, replace: node.querySelector("#import-replace").checked });
      toast(`Imported ${r.imported} channels`); closeModal(); loadChannels();
    } catch (e) { toast(e.message, true); }
  });
  openModal("Import channels", node);
});

// ============================ CONFIGURATIONS ============================

async function loadConfigurations() {
  try {
    cachedConfigs = await api.get("/api/configurations");
    catalogNeedsDpadKeys = computeCatalogNeedsDpad(cachedChannels, cachedConfigs);
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const tbody = document.querySelector("#config-table tbody");
  if (!tbody) return;
  if (!cachedConfigs.length) {
    tbody.innerHTML = `<tr><td colspan="4"><div class="empty">No configurations yet — import babsonnexus App Play JSON from <code>adbtuner_native/configurations</code>.</div></td></tr>`;
    return;
  }
  tbody.innerHTML = "";
  cachedConfigs.forEach((cfg) => {
    const tr = el(`<tr>
      <td>${escapeHtml(cfg.name || "")}</td>
      <td class="mono">${escapeHtml(cfg.uuid || "")}</td>
      <td>${escapeHtml(cfg.version || "")}</td>
      <td class="col-actions"><button class="btn btn-sm btn-danger" data-del>Delete</button></td>
    </tr>`);
    tr.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm(`Delete configuration ${cfg.name || cfg.uuid}?`)) return;
      try {
        await api.del(`/api/configurations/${encodeURIComponent(cfg.uuid)}`);
        toast("Configuration deleted");
        loadConfigurations();
      } catch (e) { toast(e.message, true); }
    });
    tbody.appendChild(tr);
  });
}

document.getElementById("export-config-btn")?.addEventListener("click", async () => {
  try {
    const data = await api.get("/api/configurations/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "apituner-configurations.json"; a.click();
  } catch (e) { toast(e.message, true); }
});

document.getElementById("import-config-btn")?.addEventListener("click", () => {
  const node = el(`<div>
    <p class="muted">Paste one babsonnexus / ADBTuner configuration object, or an array of them (from <code>adbtuner_native/configurations/*.json</code>).</p>
    <div class="field full"><textarea id="import-config-json" rows="12" placeholder='{ "uuid": "0AppPlay-…", "tune_commands": [ … ] }'></textarea></div>
    <div class="field checkbox"><input type="checkbox" id="import-config-replace" /><label>Replace all existing configurations</label></div>
    <div class="form-actions"><button class="btn btn-ghost" data-cancel>Cancel</button><button class="btn btn-primary" data-import>Import</button></div>
  </div>`);
  node.querySelector("[data-cancel]").addEventListener("click", closeModal);
  node.querySelector("[data-import]").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(node.querySelector("#import-config-json").value); }
    catch { toast("Invalid JSON", true); return; }
    try {
      const r = await api.post("/api/configurations/import", {
        configurations: parsed,
        replace: node.querySelector("#import-config-replace").checked,
      });
      toast(`Imported ${r.imported} configuration(s)`); closeModal(); loadConfigurations();
    } catch (e) { toast(e.message, true); }
  });
  openModal("Import configurations", node);
});

// ============================ OPTIONS ============================
const OPTION_FIELDS = [
  ["tune_timeout_seconds", "Tune timeout (s)", "number", null, "Max seconds to wait for a channel to become ready"],
  ["request_timeout", "Request timeout (s)", "number", null, "HTTP timeout for Agent API calls"],
  ["release_grace_seconds", "Release grace (s)", "number", null, "Hold tuner lock briefly after stream disconnect"],
  ["stuck_tuner_timeout_seconds", "Stuck tuner timeout (s)", "number", null, "Reclaim tuners that stop making progress"],
  ["tuner_idle_timeout_seconds", "Idle reclaim (redirect) (s)", "number", null, "Reclaim tuners in redirect mode after idle"],
  ["stream_mode", "Stream mode", "select", ["proxy", "redirect"], "proxy relays MPEG-TS; redirect sends clients to the encoder"],
  ["stream_during_tune", "Stream during App Play", "bool", null, "Start the HDMI encoder stream while App Play navigates (keeps Channels connected past its ~30s timeout; you’ll briefly see the on-screen automation)"],
  ["app_play_prefer", "App Play stick preference", "select", [
    { value: "fire", label: "Fire Stick / ADB first" },
    { value: "google_tv", label: "Google TV / Chromecast first" },
    { value: "any", label: "No preference" },
  ], "When several tuners can run App Play, prefer this D-pad path (Fire-tuned ESPN scripts usually need Fire Stick / ADB)"],
  ["wait_for_playback", "Wait for playback signal", "bool", null, "When on, wait for a playing MediaSession before accepting a tune (falls back to foreground if playback never appears)"],
  ["ready_settle_seconds", "Ready settle (s)", "number", null, "Extra wait after playback is detected before opening the HDMI stream"],
  ["stop_on_release", "Stop app on release", "bool", null, "Send HOME when the stream ends"],
  ["keep_apps_running", "Keep apps running", "bool", null, "When off, always send HOME on release"],
  ["retry_on_other_tuner", "Retry on another tuner", "bool", null, "Try another eligible tuner if a tune fails"],
  ["hdhr_enabled", "HDHomeRun emulation", "bool", null, "Appear as an HDHomeRun tuner for Channels DVR (enables Tuner Sharing)"],
  ["hdhr_friendly_name", "HDHomeRun name", "text", null, "Friendly name shown in Channels / Plex source list"],
  ["hdhr_device_id", "HDHomeRun DeviceID", "text", null, "Stable 8-char hex ID (persisted; do not change lightly)"],
  ["hdhr_port", "HDHomeRun port (optional)", "number", null, "Leave blank to use the main APITuner port"],
  ["hdhr_ssdp_enabled", "SSDP discovery", "bool", null, "Advertise via SSDP/UPnP multicast (needs host networking in Docker)"],
  ["hdhr_udp_discovery_enabled", "UDP 65001 discovery", "bool", null, "SiliconDust broadcast discovery (needs host networking in Docker)"],
  ["channels_dvr_url", "Channels DVR URL", "text", null, "LAN base URL for guide import, e.g. http://192.0.2.30:8089"],
  ["xmltv_source_device", "XMLTV source device", "text", null, "Channels device ID used as schedule source (e.g. M3U-YouTubeTV)"],
  ["xmltv_duration_seconds", "XMLTV duration (s)", "number", null, "How far ahead to pull listings (default 259200 = 3 days)"],
  ["xmltv_cache_seconds", "XMLTV cache (s)", "number", null, "How long to reuse a built /xmltv.xml response"],
];
async function loadOptions() {
  const form = document.getElementById("options-form");
  let opts = {};
  try { opts = await api.get("/api/options"); } catch (e) { toast(e.message, true); return; }
  form.innerHTML = "";
  for (const [key, label, type, choices, hint] of OPTION_FIELDS) {
    if (type === "bool") {
      const row = el(`<div class="field checkbox"><input type="checkbox" name="${key}" ${opts[key] ? "checked" : ""} /><label>${label}</label></div>`);
      if (hint) row.appendChild(el(`<div class="hint">${hint}</div>`));
      form.appendChild(row);
    } else if (type === "select") {
      const f = el(`<div class="field"><label>${label}</label><select name="${key}"></select>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`);
      choices.forEach((ch) => {
        const value = typeof ch === "object" && ch ? ch.value : ch;
        const text = typeof ch === "object" && ch ? (ch.label || ch.value) : ch;
        const o = el(`<option value="${escapeAttr(value)}">${escapeHtml(text)}</option>`);
        if (opts[key] === value) o.selected = true;
        f.querySelector("select").appendChild(o);
      });
      form.appendChild(f);
    } else if (type === "text") {
      const val = opts[key] == null ? "" : opts[key];
      form.appendChild(el(`<div class="field"><label>${label}</label><input type="text" name="${key}" value="${escapeAttr(val)}" />${hint ? `<div class="hint">${hint}</div>` : ""}</div>`));
    } else {
      const val = opts[key] == null ? "" : opts[key];
      form.appendChild(el(`<div class="field"><label>${label}</label><input type="number" step="any" name="${key}" value="${val}" />${hint ? `<div class="hint">${hint}</div>` : ""}</div>`));
    }
  }
}
document.getElementById("save-options").addEventListener("click", async () => {
  const form = document.getElementById("options-form");
  const payload = {};
  for (const [key, , type] of OPTION_FIELDS) {
    const input = form.querySelector(`[name="${key}"]`);
    if (!input) continue;
    if (type === "bool") payload[key] = input.checked;
    else if (type === "number") {
      const raw = String(input.value ?? "").trim();
      payload[key] = raw === "" ? null : Number(raw);
    } else payload[key] = input.value;
  }
  try {
    await api.put("/api/options", payload);
    toast("Options saved (restart required for discovery changes)");
    const status = await api.get("/api/status");
    initHdhr(status);
  } catch (e) { toast(e.message, true); }
});

document.getElementById("download-diagnostics").addEventListener("click", () => {
  // Navigating to the endpoint triggers Content-Disposition download.
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
  const free = data.tuners.length - active;
  const errors = data.tuners.filter((t) => t.last_error).length;

  const hdhrBits = data.hdhr && data.hdhr.enabled
    ? ` · HDHR ${data.hdhr.tuner_count} tuner${data.hdhr.tuner_count === 1 ? "" : "s"}`
    : "";
  document.getElementById("status-meta").textContent =
    `v${data.version} · ${data.options.stream_mode} stream mode${hdhrBits} · updates every 3s`;

  document.getElementById("app-version").textContent = `v${data.version}`;
  initHdhr(data);

  stats.innerHTML = `
    <div class="stat-card"><div class="stat-label">Tuners</div><div class="stat-value">${data.tuners.length}</div></div>
    <div class="stat-card"><div class="stat-label">Active</div><div class="stat-value amber">${active}</div></div>
    <div class="stat-card"><div class="stat-label">Available</div><div class="stat-value green">${free}</div></div>
    <div class="stat-card"><div class="stat-label">Channels</div><div class="stat-value accent">${data.channel_count}</div></div>
    ${errors ? `<div class="stat-card"><div class="stat-label">Errors</div><div class="stat-value" style="color:var(--red)">${errors}</div></div>` : ""}`;

  if (!data.tuners.length) {
    list.innerHTML = `<div class="empty-state"><div class="empty-state-icon">●</div><h3>No tuners</h3><p>Add tuners to see live activity here.</p></div>`;
    return;
  }
  list.innerHTML = "";
  for (const s of data.tuners) {
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
        <span class="badge ${s.locked ? "warn" : "on"}">${s.locked ? "Tuning" : "Idle"}</span>
      </div>
      <div class="card-meta">
        ${s.locked ? `<div class="card-row"><span class="label">Channel</span><span class="value"><strong>${s.channel_number ?? "?"}</strong> · ${escapeHtml(s.channel_name || "")}</span></div>` : ""}
        ${s.tune_id ? `<div class="card-row"><span class="label">Tune ID</span><span class="value mono">${s.tune_id}</span></div>` : ""}
        ${s.last_tune_seconds != null ? `<div class="card-row"><span class="label">Last tune</span><span class="value">${s.last_tune_seconds.toFixed(1)}s</span></div>` : ""}
        ${s.locked && s.bytes_transferred ? `<div class="card-row"><span class="label">Streamed</span><span class="value">${fmtBytes(s.bytes_transferred)}</span></div>` : ""}
        ${s.locked && s.lock_seconds != null ? `<div class="card-row"><span class="label">Lock time</span><span class="value">${s.lock_seconds}s</span></div>` : ""}
        ${s.last_error ? `<div class="card-row"><span class="label">Error</span><span class="value"><span class="badge off">${escapeHtml(s.last_error)}</span></span></div>` : ""}
      </div>`;
    list.appendChild(card);
  }
}
function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}

// ---- misc ----
document.getElementById("modal-close").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escapeAttr(s) { return escapeHtml(s); }

// ---- init ----
initM3u();
loadTuners();
api.get("/api/status").then((d) => {
  const elVer = document.getElementById("app-version");
  if (elVer) elVer.textContent = `v${d.version}`;
  initHdhr(d);
  if (d.agent_apk_url) {
    document.querySelectorAll(".agent-apk-link").forEach((a) => {
      a.href = d.agent_apk_url;
    });
  }
}).catch(() => {});
