const test = require("node:test");
const assert = require("node:assert/strict");
const utils = require("../../apituner/web/ui-utils.js");

test("compareChannelNumbers orders integers and dotted subchannels numerically", () => {
  const sorted = ["10", "2.10", "2.2", "2", "100", "2.1"].sort(utils.compareChannelNumbers);
  assert.deepEqual(sorted, ["2", "2.1", "2.2", "2.10", "10", "100"]);
});

test("looksLikeFireTv matches Amazon / Fire / AFT hardware signatures", () => {
  assert.equal(utils.looksLikeFireTv({ manufacturer: "Amazon", model: "AFTMM" }), true);
  assert.equal(utils.looksLikeFireTv({ manufacturer: "Google", model: "Chromecast" }), false);
  assert.equal(utils.looksLikeFireTv(null), false);
});

test("expandKeyMacroTokens normalizes delimiters and KEYCODE_ prefix", () => {
  assert.deepEqual(
    utils.expandKeyMacroTokens(["KEYCODE_DPAD_DOWN; wait 1.5, enter"]),
    ["DPAD_DOWN", "WAIT 1.5", "ENTER"],
  );
  assert.deepEqual(utils.expandKeyMacroTokens(null), []);
});

test("channelNeedsDpad detects App Play, D-pad macros, and who's-watching prompts", () => {
  assert.equal(utils.channelNeedsDpad({ url: "https://tv.youtube.com/watch/abc" }, {}), false);
  assert.equal(utils.channelNeedsDpad({ url: "https://tv.youtube.com/watch/abc", key_macro: ["HOME", "WAIT 1.5"] }, {}), false);
  assert.equal(utils.channelNeedsDpad({ url: "https://tv.youtube.com/watch/abc", key_macro: ["DPAD_CENTER"] }, {}), true);
  assert.equal(utils.channelNeedsDpad({ url: "3", configuration_uuid: "cfg-1" }, {}), true);
  assert.equal(
    utils.channelNeedsDpad(
      { url: "https://play.max.com/live/1", configuration_uuid: "cfg-max" },
      { "cfg-max": { global_options: { check_for_and_clear_whos_watching_prompts: true } } },
    ),
    true,
  );
});

test("formatDetail flattens FastAPI validation arrays and strings", () => {
  assert.equal(utils.formatDetail("Tuner not found"), "Tuner not found");
  assert.equal(
    utils.formatDetail([
      { loc: ["body", "options", "hdhr_port"], msg: "must be <= 65535" },
      { loc: ["body", "name"], msg: "required" },
    ]),
    "options.hdhr_port: must be <= 65535; name: required",
  );
});

test("escapeHtml and fmtBytes produce safe, readable output", () => {
  assert.equal(utils.escapeHtml(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  assert.equal(utils.fmtBytes(512), "512 B");
  assert.equal(utils.fmtBytes(2048), "2.0 KB");
  assert.equal(utils.fmtBytes(5 * 1024 * 1024), "5.0 MB");
});
