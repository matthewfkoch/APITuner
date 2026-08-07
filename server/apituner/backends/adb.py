"""Network ADB control backend (Fire TV App Play fallback).

Day-to-day Google TV tuning stays ADB-free. Fire OS 7 sticks often lack the
Fire TV Remote HTTPS API (:8080), but network ADB still works — use this
backend to run babsonnexus App Play D-pad scripts on those devices.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Optional

from ..models import DEFAULT_ADB_PORT, Tuner
from .base import (
    BackendUnavailable,
    Capabilities,
    ControlBackend,
    DeviceInfo,
    PlaybackState,
)

logger = logging.getLogger(__name__)


def _monkey_aborted(output: str) -> bool:
    lowered = (output or "").lower()
    return "no activities" in lowered or "monkey aborted" in lowered


class AdbBackend(ControlBackend):
    """Drive a device over network ADB (full D-pad + real force-stop)."""

    capabilities = Capabilities(
        keys=True,
        dpad=True,
        shell=True,
        current_app=True,
        playback_state=False,
        power=False,
        app_list=True,
        install=False,
    )

    def __init__(self, tuner: Tuner, *, request_timeout: float = 15.0) -> None:
        self._tuner = tuner
        self._host = (tuner.control.host or "").split(":", 1)[0].strip()
        self._port = tuner.control.port or DEFAULT_ADB_PORT
        self._serial = f"{self._host}:{self._port}"
        self._timeout = request_timeout
        self._adb: Optional[str] = None
        self._connected = False

    def _adb_bin(self) -> str:
        if self._adb:
            return self._adb
        path = shutil.which("adb")
        if not path:
            raise BackendUnavailable(
                "adb not found on the APITuner host. Install Android "
                "platform-tools (or use the Docker image, which includes adb)."
            )
        self._adb = path
        return path

    async def _run(self, *args: str, timeout: Optional[float] = None) -> tuple[int, str]:
        adb = self._adb_bin()
        proc = await asyncio.create_subprocess_exec(
            adb,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out_b, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout or self._timeout
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise BackendUnavailable(f"adb timed out: adb {' '.join(args)}") from exc
        text = (out_b or b"").decode("utf-8", errors="replace").strip()
        return proc.returncode or 0, text

    async def connect(self) -> None:
        if not self._host:
            raise BackendUnavailable("Tuner host is empty")
        code, out = await self._run("connect", self._serial, timeout=20.0)
        lowered = (out or "").lower()
        if "unauthorized" in lowered:
            raise BackendUnavailable(
                f"ADB unauthorized for {self._serial}; accept the RSA prompt on the TV"
            )
        if code != 0 and "connected" not in lowered and "already connected" not in lowered:
            raise BackendUnavailable(f"adb connect failed: {out or code}")
        # Confirm device state.
        code, state = await self._run("-s", self._serial, "get-state", timeout=10.0)
        if "device" not in (state or "").lower():
            raise BackendUnavailable(
                f"ADB device {self._serial} not ready (state={state!r}). "
                "Enable network debugging and accept the RSA prompt."
            )
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def health(self) -> bool:
        try:
            await self.connect()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def run_shell(self, command: str) -> str:
        """Run `adb shell <command>` and return combined stdout."""
        await self.connect()
        # Pass command as a single shell string so spaces/quotes work like ADBTuner.
        code, out = await self._run(
            "-s", self._serial, "shell", command, timeout=max(30.0, self._timeout)
        )
        if code != 0:
            raise BackendUnavailable(f"adb shell failed ({code}): {command!r} → {out}")
        return out

    async def get_info(self) -> DeviceInfo:
        await self.connect()

        async def prop(name: str) -> Optional[str]:
            try:
                _, out = await self._run(
                    "-s", self._serial, "shell", "getprop", name, timeout=10.0
                )
                return out.strip() or None
            except Exception:  # noqa: BLE001
                return None

        model = await prop("ro.product.model")
        manufacturer = await prop("ro.product.manufacturer")
        os_version = await prop("ro.build.version.release")
        sdk_raw = await prop("ro.build.version.sdk")
        sdk_int = None
        if sdk_raw and sdk_raw.isdigit():
            sdk_int = int(sdk_raw)

        packages: list[str] = []
        try:
            _, out = await self._run(
                "-s", self._serial, "shell", "pm", "list", "packages", timeout=30.0
            )
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    packages.append(line.split(":", 1)[1].strip())
        except Exception:  # noqa: BLE001
            pass

        return DeviceInfo(
            model=model,
            manufacturer=manufacturer,
            os_version=os_version,
            sdk_int=sdk_int,
            packages=packages,
        )

    async def launch(
        self,
        *,
        package: str,
        deeplink: Optional[str] = None,
        component: Optional[str] = None,
        action: Optional[str] = None,
        extras: Optional[str] = None,
    ) -> None:
        await self.connect()
        act = action or "android.intent.action.VIEW"
        if deeplink:
            # Package-pinned VIEW, same spirit as ADBTuner am start -d … pkg
            cmd = f"am start -W -a {act} -d '{deeplink}'"
            if component:
                cmd += f" -n '{package}/{component}'" if "/" not in component else f" -n '{component}'"
            else:
                cmd += f" '{package}'"
            if extras:
                for pair in extras.split(","):
                    if ":" not in pair:
                        continue
                    k, v = pair.split(":", 1)
                    cmd += f" --es '{k.strip()}' '{v.strip()}'"
            await self.run_shell(cmd)
            return

        if component:
            comp = component if "/" in component else f"{package}/{component}"
            await self.run_shell(f"am start -W -n '{comp}'")
            return

        # Launch launcher activity for the package (App Play open_app).
        # Android TV / Fire apps (ESPN, etc.) often expose only LEANBACK_LAUNCHER;
        # phone apps use LAUNCHER. Try Leanback first (APITuner targets TV), then
        # phone LAUNCHER. monkey often exits 0 even when it prints
        # "No activities found … monkey aborted", so inspect stdout and fail hard
        # if both categories abort (missing / non-launchable package).
        await self._monkey_open_app(package)

    async def _monkey_open_app(self, package: str) -> None:
        """Open ``package`` via monkey; raise if no launcher activity exists."""
        categories = (
            "android.intent.category.LEANBACK_LAUNCHER",
            "android.intent.category.LAUNCHER",
        )
        last_out = ""
        for category in categories:
            try:
                out = await self.run_shell(f"monkey -p {package} -c {category} 1")
            except BackendUnavailable as exc:
                # Some devices return non-zero on abort; keep trying the next category.
                last_out = str(exc)
                logger.info(
                    "ADB monkey %s failed for %s (%s); trying next category",
                    category,
                    package,
                    exc,
                )
                continue
            last_out = out or ""
            if not _monkey_aborted(last_out):
                return
            logger.info(
                "ADB monkey %s aborted for %s; trying next category",
                category,
                package,
            )
        raise BackendUnavailable(
            f"ADB could not open {package} (no LAUNCHER or LEANBACK_LAUNCHER "
            f"activity). Install the app or fix package_name / "
            f"alternate_package_name. Last monkey output: {last_out[:200]!r}"
        )

    async def send_key(self, key: str) -> None:
        raw = key.strip()
        if raw.upper().startswith("KEYCODE_"):
            code = raw.upper()
        elif raw.isdigit():
            code = raw
        else:
            code = f"KEYCODE_{raw.upper()}"
        await self.run_shell(f"input keyevent {code}")

    async def force_stop(self, package: str) -> None:
        await self.run_shell(f"am force-stop {package}")

    async def current_app(self) -> Optional[str]:
        await self.connect()
        # dumpsys window is widely available; parse mCurrentFocus / mFocusedApp.
        try:
            out = await self.run_shell(
                "dumpsys activity activities | grep -E 'mResumedActivity|mFocusedApp' | head -3"
            )
        except BackendUnavailable:
            return None
        for token in out.replace("/", " ").split():
            if token.count(".") >= 2 and not token.startswith("ActivityRecord"):
                # Likely a package name
                pkg = token.split()[0] if " " in token else token
                if pkg.startswith("com.") or pkg.startswith("org.") or pkg.startswith("tv."):
                    return pkg.strip("{}")
        # Fallback: monkey-style parse
        for line in out.splitlines():
            if "u0 " in line and "/." in line:
                try:
                    part = line.split("u0 ", 1)[1]
                    pkg = part.split("/", 1)[0].strip()
                    if pkg:
                        return pkg
                except IndexError:
                    continue
        return None

    async def playback_state(self) -> PlaybackState:
        return PlaybackState.UNKNOWN

    async def stop(self) -> None:
        try:
            await self.send_key("HOME")
        except Exception:  # noqa: BLE001
            pass

    async def list_apps(self) -> list[dict[str, str]]:
        info = await self.get_info()
        return [{"name": p, "packageName": p} for p in info.packages]
