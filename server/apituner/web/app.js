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
async function handle(resp) {
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!resp.ok) throw new Error((data && data.detail) || resp.statusText || "Request failed");
  return data;
}

// ---- Capability definitions (label + tooltip; optional live status from /api/info) ----
const CAP_DEFS = {
  http_agent: [
    { label: "Launch channels", hint: "Opens the streaming app and deep link when Channels tunes a station.", always: true },
    { label: "Foreground app", hint: "Detects which app is on screen after a tune. Requires Usage Access on the device.", cap: "current_app" },
    { label: "Playback check", hint: "Confirms video is playing before the HDMI stream is handed to Channels. Requires Notification Access.", cap: "playback_state" },
    { label: "Send keys", hint: "Sends BACK, HOME, and RECENTS through the Agent. Requires Accessibility on the device.", cap: "keys" },
    { label: "App list", hint: "Lists installed apps on the device — used when picking a package while editing channels.", cap: "app_list" },
    { label: "Install APKs", hint: "Can sideload APKs to the device through the Agent (advanced).", cap: "install" },
  ],
  androidtv_remote: [
    { label: "Send keys", hint: "Sends BACK, HOME, and other remote key presses through the Google TV Remote protocol.", cap: "keys" },
    { label: "Foreground app", hint: "Reads which app is in the foreground after a tune.", cap: "current_app" },
    { label: "Playback check", hint: "Best-effort playback detection. May be limited compared to the Agent APK.", cap: "playback_state" },
  ],
};

// ---- UI utilities ----
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }
function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), 3200);
}
function openModal(title, node) {
  document.getElementById("modal-title").textContent = title;
  const body = document.getElementById("modal-body");
  body.innerHTML = ""; body.appendChild(node);
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }

// ---- Navigation ----
document.querySelectorAll(".nav-item").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    if (tab.dataset.tab === "status") startStatusPolling(); else stopStatusPolling();
    if (tab.dataset.tab === "channels") loadChannels();
    if (tab.dataset.tab === "tuners") loadTuners();
    if (tab.dataset.tab === "options") loadOptions();
  });
});

// ---- M3U URL ----
function initM3u() {
  const url = `${location.origin}/channels.m3u`;
  document.getElementById("m3u-url").value = url;
}
document.getElementById("copy-m3u").addEventListener("click", () => {
  const input = document.getElementById("m3u-url");
  navigator.clipboard.writeText(input.value).then(() => toast("M3U URL copied"));
});

// ============================ TUNERS ============================
let cachedChannels = [];

async function loadTuners() {
  const list = document.getElementById("tuner-list");
  let tuners = [];
  try { tuners = await api.get("/api/tuners"); } catch (e) { toast(e.message, true); return; }
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
  list.innerHTML = "";
  for (const t of tuners) {
    const backendLabel = t.control.type === "http_agent" ? "Agent APK" : "TV Remote";
    const card = el(`<article class="card"></article>`);
    card.innerHTML = `
      <div class="card-head">
        <div>
          <div class="card-title">${escapeHtml(t.name)}</div>
          <div class="card-sub">
            <span class="backend-pill">${backendLabel}</span>
            &nbsp;·&nbsp; ${escapeHtml(t.control.host)}${t.control.port ? ":" + t.control.port : ""}
          </div>
        </div>
        <div class="card-badges">
          <span class="badge ${t.enabled ? "on" : "off"}">${t.enabled ? "Enabled" : "Disabled"}</span>
          <span class="badge muted" data-health title="Checking whether the device is reachable…">Checking…</span>
        </div>
      </div>
      <div class="card-meta">
        <div class="card-row"><span class="label">Encoder</span><span class="value mono">${escapeHtml(t.stream_endpoint)}</span></div>
      </div>
      <div class="cap-section">
        <div class="cap-label">What this backend can do</div>
        <div class="badges" data-badges></div>
      </div>
      <div class="card-actions">
        <button class="btn btn-sm btn-secondary" data-act="health" title="Ping the device to verify the Agent APK or TV remote is reachable on the network">Recheck connection</button>
        ${t.control.type === "androidtv_remote" ? `<button class="btn btn-sm btn-secondary" data-act="pair">Pair</button><span data-pair-status class="badge muted">…</span>` : ""}
        <button class="btn btn-sm btn-ghost" data-act="edit">Edit</button>
        <button class="btn btn-sm btn-danger" data-act="delete">Delete</button>
      </div>`;
    const badges = card.querySelector("[data-badges]");
    renderCapabilityBadges(badges, t.control.type);
    const healthBtn = card.querySelector('[data-act="health"]');
    const healthBadge = card.querySelector("[data-health]");
    const setHealth = (online) => {
      healthBadge.className = `badge ${online ? "on" : "off"}`;
      healthBadge.textContent = online ? "Reachable" : "Unreachable";
      healthBadge.title = online
        ? "Device responded to a health check"
        : "Device did not respond — check IP, Agent APK, or network";
      card.classList.toggle("card-online", online);
      card.classList.toggle("card-offline", !online);
    };
    const runHealthCheck = async () => {
      healthBtn.disabled = true;
      healthBtn.textContent = "Checking…";
      healthBadge.className = "badge muted";
      healthBadge.textContent = "Checking…";
      try {
        const r = await api.get(`/api/tuners/${t.id}/health`);
        setHealth(r.online);
        if (r.online) await refreshCapabilityStatus(badges, t);
      } catch (err) {
        setHealth(false);
        toast(err.message, true);
      }
      healthBtn.disabled = false;
      healthBtn.textContent = "Recheck connection";
    };
    healthBtn.addEventListener("click", runHealthCheck);
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
    list.appendChild(card);
    runHealthCheck();
  }
}

function renderCapabilityBadges(container, backendType) {
  container.innerHTML = "";
  (CAP_DEFS[backendType] || []).forEach((def) => {
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
  const t = existing || { name: "", control: { type: "http_agent", host: "", port: 9092, pair_port: null, token: "" }, stream_endpoint: "", enabled: true };
  const form = el(`<form class="form-grid"></form>`);
  form.innerHTML = `
    <div class="field full"><label>Name</label><input name="name" value="${escapeAttr(t.name)}" required /></div>
    <div class="field"><label>Backend</label>
      <select name="type">
        <option value="http_agent">http_agent (Agent APK) — recommended</option>
        <option value="androidtv_remote">androidtv_remote (Google TV Remote)</option>
      </select>
    </div>
    <div class="field"><label>Host / IP</label><input name="host" value="${escapeAttr(t.control.host)}" required /></div>
    <div class="field"><label>Port <span class="hint">(blank = default)</span></label><input name="port" type="number" value="${t.control.port ?? ""}" /></div>
    <div class="field" data-remote><label>Pair port <span class="hint">(remote, default 6467)</span></label><input name="pair_port" type="number" value="${t.control.pair_port ?? ""}" /></div>
    <div class="field" data-agent><label>Auth token <span class="hint">(agent, optional)</span></label><input name="token" value="${escapeAttr(t.control.token || "")}" /></div>
    <div class="field full"><label>Encoder stream URL <span class="hint">(HDMI encoder MPEG-TS)</span></label><input name="stream_endpoint" value="${escapeAttr(t.stream_endpoint)}" placeholder="http://192.168.1.41:8090/stream0" required /></div>
    <div class="field checkbox full"><input type="checkbox" name="enabled" ${t.enabled ? "checked" : ""} /><label>Enabled</label></div>
    <div class="form-actions full"><button type="button" class="btn btn-ghost" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>`;
  const typeSel = form.querySelector('[name="type"]');
  typeSel.value = t.control.type;
  const syncType = () => {
    form.querySelector("[data-remote]").style.display = typeSel.value === "androidtv_remote" ? "" : "none";
    form.querySelector("[data-agent]").style.display = typeSel.value === "http_agent" ? "" : "none";
  };
  typeSel.addEventListener("change", syncType); syncType();
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
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
    };
    try {
      if (existing) await api.put(`/api/tuners/${existing.id}`, { id: existing.id, ...payload });
      else await api.post("/api/tuners", payload);
      toast("Tuner saved"); closeModal(); loadTuners();
    } catch (err) { toast(err.message, true); }
  });
  openModal(existing ? "Edit tuner" : "Add tuner", form);
}
document.getElementById("add-tuner-btn").addEventListener("click", () => tunerForm(null));

async function pairFlow(t) {
  const node = el(`<div>
    <p class="muted">Starting pairing with <b>${escapeHtml(t.name)}</b>. A PIN will appear on the TV screen.</p>
    <div class="field full"><label>PIN from TV</label><input id="pair-pin" placeholder="e.g. A1B2C3" /></div>
    <div class="form-actions"><button class="btn btn-ghost" data-cancel>Cancel</button><button class="btn btn-primary" data-finish>Complete pairing</button></div>
    <p id="pair-msg" class="muted"></p>
  </div>`);
  node.querySelector("[data-cancel]").addEventListener("click", closeModal);
  const msg = node.querySelector("#pair-msg");
  openModal("Pair Android TV", node);
  try { await api.post(`/api/tuners/${t.id}/pair/start`); msg.textContent = "Enter the PIN shown on the TV, then click Complete pairing."; }
  catch (e) { msg.textContent = "Failed to start pairing: " + e.message; }
  node.querySelector("[data-finish]").addEventListener("click", async () => {
    const pin = node.querySelector("#pair-pin").value.trim();
    if (!pin) { msg.textContent = "Please enter the PIN."; return; }
    try { await api.post(`/api/tuners/${t.id}/pair/finish`, { pin }); toast("Paired successfully"); closeModal(); loadTuners(); }
    catch (e) { msg.textContent = "Pairing failed: " + e.message; }
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
        tunerForm({
          name: d.name,
          control: { type: "http_agent", host: d.host, port: d.port || 9092, pair_port: null, token: "" },
          stream_endpoint: "",
          enabled: true,
        });
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

function renderChannels(channels) {
  const tbody = document.querySelector("#channel-table tbody");
  const q = (document.getElementById("channel-search")?.value || "").trim().toLowerCase();
  const filtered = channels.filter((c) => channelMatchesQuery(c, q));
  const countEl = document.getElementById("channel-count");
  if (countEl) {
    countEl.textContent = q
      ? `Showing ${filtered.length} of ${channels.length}`
      : `${channels.length} channel${channels.length === 1 ? "" : "s"}`;
  }
  tbody.innerHTML = "";
  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">${channels.length ? "No channels match your search." : "No channels yet — import an ADBTuner export or add one manually."}</div></td></tr>`;
    return;
  }
  for (const c of filtered) {
    const tr = el(`<tr class="row-clickable" title="Click to edit channel">
      <td><span class="ch-num">${c.number}</span></td>
      <td><strong>${escapeHtml(c.name)}</strong></td>
      <td class="muted">${escapeHtml(c.provider_name || "—")}</td>
      <td class="mono">${escapeHtml(c.package_name)}</td>
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
      catch (e) { toast(e.message, true); }
    });
    tbody.appendChild(tr);
  }
}

async function loadChannels() {
  try { cachedChannels = await api.get("/api/channels"); } catch (e) { toast(e.message, true); return; }
  cachedChannels.sort((a, b) => a.number - b.number);
  renderChannels(cachedChannels);
}

document.getElementById("channel-search")?.addEventListener("input", () => {
  renderChannels(cachedChannels);
});

function channelForm(existing) {
  const c = existing || { number: "", name: "", provider_name: "", package_name: "", alternate_package_name: "", component: "", url: "", action: "android.intent.action.VIEW", extra_string: "", key_macro: [], compatibility_mode: false, tvc_guide_stationid: "" };
  const form = el(`<form class="form-grid"></form>`);
  form.innerHTML = `
    <div class="field"><label>Channel number</label><input name="number" type="number" value="${c.number}" ${existing ? "readonly" : ""} required /></div>
    <div class="field"><label>Name</label><input name="name" value="${escapeAttr(c.name)}" required /></div>
    <div class="field"><label>Provider</label><input name="provider_name" value="${escapeAttr(c.provider_name || "")}" /></div>
    <div class="field"><label>Gracenote station id</label><input name="tvc_guide_stationid" value="${escapeAttr(c.tvc_guide_stationid || "")}" /></div>
    <div class="field"><label>Package name</label><input name="package_name" value="${escapeAttr(c.package_name)}" required /></div>
    <div class="field"><label>Alternate package</label><input name="alternate_package_name" value="${escapeAttr(c.alternate_package_name || "")}" /></div>
    <div class="field full"><label>Deep link URL <span class="hint">(intent data)</span></label><input name="url" value="${escapeAttr(c.url || "")}" placeholder="https://... or scheme://..." /></div>
    <div class="field"><label>Action</label><input name="action" value="${escapeAttr(c.action || "android.intent.action.VIEW")}" /></div>
    <div class="field"><label>Component <span class="hint">(agent; Android 12+)</span></label><input name="component" value="${escapeAttr(c.component || "")}" /></div>
    <div class="field full"><label>Intent extras <span class="hint">(agent; key:value,key:value)</span></label><input name="extra_string" value="${escapeAttr(c.extra_string || "")}" /></div>
    <div class="field full"><label>Key macro <span class="hint">(remote; comma-separated keys sent after launch, e.g. DPAD_CENTER,DPAD_DOWN)</span></label><input name="key_macro" value="${escapeAttr((c.key_macro || []).join(","))}" /></div>
    <div class="field checkbox full"><input type="checkbox" name="compatibility_mode" ${c.compatibility_mode ? "checked" : ""} /><label>Compatibility mode (stop app before launch)</label></div>
    <div class="field full"><label>Fill package from a tuner's installed apps</label>
      <select id="app-picker-tuner"><option value="">Select a tuner…</option></select>
      <div id="app-picker" class="app-picker hidden"></div>
    </div>
    <div class="form-actions full"><button type="button" class="btn btn-ghost" data-cancel>Cancel</button><button type="submit" class="btn btn-primary">Save</button></div>`;
  form.querySelector("[data-cancel]").addEventListener("click", closeModal);
  // App picker
  populateTunerSelect(form.querySelector("#app-picker-tuner"));
  form.querySelector("#app-picker-tuner").addEventListener("change", async (e) => {
    const picker = form.querySelector("#app-picker");
    if (!e.target.value) { picker.classList.add("hidden"); return; }
    picker.classList.remove("hidden"); picker.innerHTML = `<div class="muted">Loading apps…</div>`;
    try {
      const apps = await api.get(`/api/tuners/${e.target.value}/apps`);
      if (!apps.length) { picker.innerHTML = `<div class="muted">No app list available for this backend.</div>`; return; }
      picker.innerHTML = "";
      apps.forEach((a) => {
        const row = el(`<div><b>${escapeHtml(a.name || a.packageName)}</b> <span class="mono">${escapeHtml(a.packageName)}</span></div>`);
        row.addEventListener("click", () => { form.querySelector('[name="package_name"]').value = a.packageName; });
        picker.appendChild(row);
      });
    } catch (err) { picker.innerHTML = `<div class="muted">Could not load apps: ${escapeHtml(err.message)}</div>`; }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const km = (fd.get("key_macro") || "").split(",").map((s) => s.trim()).filter(Boolean);
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
    };
    try {
      if (existing) await api.put(`/api/channels/${existing.number}`, payload);
      else await api.post("/api/channels", payload);
      toast("Channel saved"); closeModal(); loadChannels();
    } catch (err) { toast(err.message, true); }
  });
  openModal(existing ? `Edit channel ${existing.number}` : "Add channel", form);
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
    <p class="muted">Paste an ADBTuner (or APITuner) channel-list JSON array.</p>
    <div class="field full"><textarea id="import-json" rows="10" placeholder="[ { &quot;number&quot;: 9000, ... } ]"></textarea></div>
    <div class="field checkbox"><input type="checkbox" id="import-replace" /><label>Replace all existing channels</label></div>
    <div class="form-actions"><button class="btn btn-ghost" data-cancel>Cancel</button><button class="btn btn-primary" data-import>Import</button></div>
  </div>`);
  node.querySelector("[data-cancel]").addEventListener("click", closeModal);
  node.querySelector("[data-import]").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse(node.querySelector("#import-json").value); }
    catch { toast("Invalid JSON", true); return; }
    try {
      const r = await api.post("/api/import", { channels: parsed, replace: node.querySelector("#import-replace").checked });
      toast(`Imported ${r.imported} channels`); closeModal(); loadChannels();
    } catch (e) { toast(e.message, true); }
  });
  openModal("Import channels", node);
});

// ============================ OPTIONS ============================
const OPTION_FIELDS = [
  ["tune_timeout_seconds", "Tune timeout (s)", "number", null, "Max seconds to wait for a channel to become ready"],
  ["request_timeout", "Request timeout (s)", "number", null, "HTTP timeout for Agent API calls"],
  ["release_grace_seconds", "Release grace (s)", "number", null, "Hold tuner lock briefly after stream disconnect"],
  ["stuck_tuner_timeout_seconds", "Stuck tuner timeout (s)", "number", null, "Reclaim tuners that stop making progress"],
  ["tuner_idle_timeout_seconds", "Idle reclaim (redirect) (s)", "number", null, "Reclaim tuners in redirect mode after idle"],
  ["stream_mode", "Stream mode", "select", ["proxy", "redirect"], "proxy relays MPEG-TS; redirect sends Channels to the encoder"],
  ["wait_for_playback", "Wait for playback signal", "bool", null, "Prefer playback/foreground before accepting a tune"],
  ["stop_on_release", "Stop app on release", "bool", null, "Send HOME when the stream ends"],
  ["keep_apps_running", "Keep apps running", "bool", null, "When off, always send HOME on release"],
  ["retry_on_other_tuner", "Retry on another tuner", "bool", null, "Try another eligible tuner if a tune fails"],
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
      choices.forEach((ch) => { const o = el(`<option value="${ch}">${ch}</option>`); if (opts[key] === ch) o.selected = true; f.querySelector("select").appendChild(o); });
      form.appendChild(f);
    } else {
      form.appendChild(el(`<div class="field"><label>${label}</label><input type="number" step="any" name="${key}" value="${opts[key]}" />${hint ? `<div class="hint">${hint}</div>` : ""}</div>`));
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
    else if (type === "number") payload[key] = Number(input.value);
    else payload[key] = input.value;
  }
  try { await api.put("/api/options", payload); toast("Options saved"); }
  catch (e) { toast(e.message, true); }
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

  document.getElementById("status-meta").textContent =
    `v${data.version} · ${data.options.stream_mode} stream mode · updates every 3s`;

  document.getElementById("app-version").textContent = `v${data.version}`;

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
}).catch(() => {});
