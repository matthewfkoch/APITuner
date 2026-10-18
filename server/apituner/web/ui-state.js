"use strict";

// Presentation only: keep reachability, allocation, pairing and package evidence separate.
const APITunerUI = {
  tunerIssues(state) {
    if (state.enabled === "false") return [];
    const issues = [];
    if (state.health === "offline") issues.push("Device unreachable");
    if (state.needsKeys === "true") issues.push("D-pad setup needed");
    if (state.pair === "unpaired") issues.push("Remote not paired");
    if (state.pair === "unknown") issues.push("Pairing status unknown");
    if (state.update === "true") issues.push("Agent update available");
    if (state.error) issues.push(`Last error: ${state.error}`);
    return issues;
  },
  channelConfiguration(channel, coverage) {
    if (!channel.package_name && !channel.alternate_package_name) return { label: "Package not set", warning: true };
    if (coverage?.status === "missing") return { label: "App not installed", warning: true };
    if (coverage?.status === "partial") return { label: "App on some tuners", warning: true };
    if (coverage?.status === "ok") return { label: "App available", warning: false };
    return { label: "Not checked", warning: false };
  },
};
if (typeof module !== "undefined" && module.exports) module.exports = APITunerUI;
