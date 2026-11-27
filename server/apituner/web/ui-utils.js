"use strict";

// Shared pure utilities, capability definitions, and JSON API helpers.
// Loaded in the browser before app.js and importable from Node tests.

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

function channelNumberSortKey(number) {
  const text = String(number ?? "").trim();
  const dot = text.indexOf(".");
  if (dot >= 0) {
    return [parseInt(text.slice(0, dot), 10) || 0, parseInt(text.slice(dot + 1), 10) || 0, text];
  }
  return [parseInt(text, 10) || 0, 0, text];
}

function compareChannelNumbers(a, b) {
  const ka = channelNumberSortKey(a);
  const kb = channelNumberSortKey(b);
  for (let i = 0; i < 3; i += 1) {
    if (ka[i] !== kb[i]) return ka[i] < kb[i] ? -1 : 1;
  }
  return 0;
}

const CAP_DEFS = {
  http_agent: [
    { label: "Launch channels", hint: "Opens the streaming app when a station is tuned.", always: true },
    { label: "Foreground app", hint: "Sees which app is on screen. Needs Usage Access.", cap: "current_app" },
    { label: "Playback check", hint: "Waits for playback before the HDMI stream. Needs Notification Access.", cap: "playback_state" },
    { label: "Send keys", hint: "Back, Home, and Recents. Needs Accessibility. Not full D-pad.", cap: "keys" },
    { label: "App list", hint: "Lists installed apps for the channel editor.", cap: "app_list" },
    { label: "Install APKs", hint: "Can sideload APKs.", cap: "install" },
  ],
  androidtv_remote: [
    { label: "Send keys", hint: "Full remote including D-pad.", cap: "keys" },
    { label: "D-pad / App Play", hint: "Runs App Play navigation without ADB.", always: true },
    { label: "Foreground app", hint: "Sees which app is in the foreground.", cap: "current_app" },
    { label: "Playback check", hint: "Best-effort playback detection.", cap: "playback_state" },
  ],
  firetv_rest: [
    { label: "Send keys", hint: "D-pad, Home, and Back. No ADB.", cap: "keys" },
    { label: "D-pad / App Play", hint: "App Play on Fire Stick / Fire TV.", always: true },
    { label: "Launch apps", hint: "Opens apps by package name.", always: true },
  ],
  adb: [
    { label: "Send keys", hint: "Full keys including D-pad over network ADB.", cap: "keys" },
    { label: "D-pad / App Play", hint: "App Play fallback on Fire OS 7.", always: true },
    { label: "Force-stop", hint: "Force-stops the app (stronger than Home).", always: true },
    { label: "App list", hint: "Lists installed packages.", cap: "app_list" },
  ],
};

const AGENT_ONLY_KEYS = new Set(["BACK", "HOME", "RECENTS", "APP_SWITCH"]);

function looksLikeFireTv(info) {
  if (!info) return false;
  const text = `${info.manufacturer || ""} ${info.model || ""}`.toLowerCase();
  return text.includes("amazon") || text.includes("fire") || /\baft[a-z0-9]*/.test(text);
}

function formatSkippedSummary(skipped, limit = 3) {
  if (!skipped || !skipped.length) return "";
  const bits = skipped.slice(0, limit).map((s) => s.name || s.reason || "row");
  const more = skipped.length > limit ? ` +${skipped.length - limit} more` : "";
  return ` (${skipped.length} skipped: ${bits.join(", ")}${more})`;
}

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
  if (uuid && !urlLooksLikeDeeplink(ch.url)) return true;
  const keys = expandKeyMacroTokens(ch.key_macro);
  if (keys.some((k) => !/^(?:SLEEP|WAIT)[\s_:]+[0-9]+(?:\.[0-9]+)?$/i.test(k) && !AGENT_ONLY_KEYS.has(k))) return true;
  if (uuid && configByUuid[uuid]) {
    const opts = configByUuid[uuid].global_options || {};
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

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function escapeAttr(s) {
  return escapeHtml(s);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    CAP_DEFS,
    AGENT_ONLY_KEYS,
    formatDetail,
    channelNumberSortKey,
    compareChannelNumbers,
    looksLikeFireTv,
    formatSkippedSummary,
    urlLooksLikeDeeplink,
    expandKeyMacroTokens,
    channelNeedsDpad,
    computeCatalogNeedsDpad,
    fmtBytes,
    escapeHtml,
    escapeAttr,
  };
}
