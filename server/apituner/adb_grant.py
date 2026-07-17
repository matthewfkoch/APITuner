"""One-time Agent permission grant via network ADB (Fire TV setup only).

Day-to-day tuning remains ADB-free through the Agent HTTP API. Fire OS hides
special-access Settings toggles for sideloaded apps, so a one-time network ADB
grant is the practical setup path. Fire Sticks run older Android builds and are
not affected by the Android 14 wired-ADB breakage that motivated APITuner.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass

logger = logging.getLogger("apituner.adb_grant")

AGENT_PACKAGE = "com.apituner.agent"
NOTIFICATION_LISTENER = (
    f"{AGENT_PACKAGE}/com.apituner.agent.control.PlaybackNotificationListener"
)
ACCESSIBILITY_SERVICE = (
    f"{AGENT_PACKAGE}/com.apituner.agent.control.KeyAccessibilityService"
)


class AdbGrantError(RuntimeError):
    """Raised when ADB is missing or a grant command fails."""


@dataclass
class GrantResult:
    overlay: bool
    usage: bool
    notification: bool
    accessibility: bool
    messages: list[str]

    def to_dict(self) -> dict:
        return {
            "overlay": self.overlay,
            "usage": self.usage,
            "notification": self.notification,
            "accessibility": self.accessibility,
            "messages": self.messages,
            # Accessibility often needs an on-device consent toggle on Fire OS.
            "success": self.overlay and self.usage and self.notification,
        }


def merge_colon_list(current: str | None, component: str) -> str:
    """Append a component to a colon-separated secure setting without duplicates."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (current or "").split(":"):
        item = raw.strip()
        if not item or item.lower() == "null":
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(item)
    if component.lower() not in seen:
        parts.append(component)
    return ":".join(parts)


def parse_adb_state(output: str) -> str:
    """Extract device state from adb get-state (or merged stderr) output."""
    for line in (output or "").splitlines():
        token = line.strip().lower()
        if token in ("device", "offline", "unauthorized", "bootloader", "recovery"):
            return token
    if "unauthorized" in (output or "").lower():
        return "unauthorized"
    return ""


def _adb_bin() -> str:
    path = shutil.which("adb")
    if not path:
        raise AdbGrantError(
            "adb not found on the APITuner host. Install Android platform-tools "
            "(or use the Docker image, which includes adb), enable network ADB "
            "debugging on the Fire TV, accept the RSA prompt, then retry."
        )
    return path


async def _run(adb: str, *args: str, timeout: float = 20.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        adb,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise AdbGrantError(f"adb timed out: adb {' '.join(args)}") from exc
    text = (out_b or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, text


async def grant_agent_permissions(host: str, *, adb_port: int = 5555) -> GrantResult:
    """Connect over network ADB and grant Agent special permissions."""
    adb = _adb_bin()
    # Strip accidental host:port so we don't build host:port:5555.
    clean_host = host.split(":", 1)[0].strip()
    if not clean_host:
        raise AdbGrantError("Tuner host is empty")
    serial = f"{clean_host}:{adb_port}"
    messages: list[str] = []

    code, out = await _run(adb, "connect", serial)
    messages.append(out or f"adb connect {serial} (exit {code})")
    lowered = out.lower()
    # "failed to authenticate" is handled below via get-state → unauthorized.
    connect_failed = (
        "refused" in lowered
        or "unable" in lowered
        or ("failed" in lowered and "authenticate" not in lowered)
    )
    if connect_failed:
        raise AdbGrantError(
            f"Could not connect to {serial}. On the Fire TV enable "
            f"Settings → My Fire TV → Developer Options → ADB debugging, then "
            f"accept the Allow USB debugging prompt. Detail: {out or code}"
        )

    code, out = await _run(adb, "-s", serial, "get-state")
    state = parse_adb_state(out)
    if state != "device":
        hint = out or f"exit {code}"
        raise AdbGrantError(
            f"Device {serial} is '{state or 'unreachable'}' (want 'device'). "
            "On the Fire TV accept Allow USB debugging (check Always allow). "
            "If APITuner runs in Docker, mount the host ~/.android directory "
            "into the container so ADB keys are shared (see docker-compose.yml). "
            f"Detail: {hint}"
        )

    async def shell(*args: str) -> tuple[bool, str]:
        c, o = await _run(adb, "-s", serial, "shell", *args)
        messages.append(o or f"{' '.join(args)} (exit {c})")
        return c == 0, o

    # Ensure the Agent APK is installed before rewriting secure settings.
    path_ok, path_out = await shell("pm", "path", AGENT_PACKAGE)
    if not path_ok or "package:" not in (path_out or "").lower():
        raise AdbGrantError(
            f"Agent APK ({AGENT_PACKAGE}) is not installed on {serial}. "
            "Install the APK first, then retry Grant permissions (ADB)."
        )

    overlay_ok, _ = await shell(
        "appops", "set", AGENT_PACKAGE, "SYSTEM_ALERT_WINDOW", "allow"
    )
    usage_ok, _ = await shell(
        "appops", "set", AGENT_PACKAGE, "GET_USAGE_STATS", "allow"
    )

    _, cur_notif = await shell(
        "settings", "get", "secure", "enabled_notification_listeners"
    )
    notif_list = merge_colon_list(cur_notif, NOTIFICATION_LISTENER)
    notif_ok, _ = await shell(
        "settings",
        "put",
        "secure",
        "enabled_notification_listeners",
        notif_list,
    )

    _, cur_a11y = await shell(
        "settings", "get", "secure", "enabled_accessibility_services"
    )
    a11y_list = merge_colon_list(cur_a11y, ACCESSIBILITY_SERVICE)
    a11y_ok, _ = await shell(
        "settings",
        "put",
        "secure",
        "enabled_accessibility_services",
        a11y_list,
    )
    if a11y_ok:
        await shell("settings", "put", "secure", "accessibility_enabled", "1")

    # Verify grants stuck (exit 0 alone is not enough on some Fire builds).
    _, overlay_state = await shell("appops", "get", AGENT_PACKAGE, "SYSTEM_ALERT_WINDOW")
    _, usage_state = await shell("appops", "get", AGENT_PACKAGE, "GET_USAGE_STATS")
    _, notif_verify = await shell(
        "settings", "get", "secure", "enabled_notification_listeners"
    )
    _, a11y_verify = await shell(
        "settings", "get", "secure", "enabled_accessibility_services"
    )

    overlay_ok = overlay_ok and "allow" in (overlay_state or "").lower()
    usage_ok = usage_ok and "allow" in (usage_state or "").lower()
    notif_ok = notif_ok and AGENT_PACKAGE in (notif_verify or "")
    a11y_ok = a11y_ok and AGENT_PACKAGE in (a11y_verify or "")

    await shell("am", "force-stop", AGENT_PACKAGE)
    await shell("am", "start", "-n", f"{AGENT_PACKAGE}/.MainActivity")

    result = GrantResult(
        overlay=overlay_ok,
        usage=usage_ok,
        notification=notif_ok,
        accessibility=a11y_ok,
        messages=messages,
    )
    logger.info(
        "ADB grant on %s: overlay=%s usage=%s notification=%s accessibility=%s",
        serial,
        overlay_ok,
        usage_ok,
        notif_ok,
        a11y_ok,
    )
    return result
