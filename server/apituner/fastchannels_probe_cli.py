#!/usr/bin/env python3
"""Reserve one idle APITuner tuner, probe FC Player via HTTP, and restore it.

Run with the server's Python environment. ADB is never used by this command.
See docs/FASTCHANNELS.md for limitations of the preview API used by this probe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from apituner.backends.http_agent import HttpAgentBackend
from apituner.fastchannels_probe import (
    PLAYER_COMPONENT, PLAYER_PACKAGE, ProbeError, http_url,
    prepare_external, prepare_preview, stop_player, wait_playing,
)
from apituner.models import Tuner


async def run(args):
    api = http_url(args.apituner_url).rstrip("/")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        async def get(path):
            response = await client.get(api + path)
            response.raise_for_status()
            return response.json()

        async def put(tuner):
            response = await client.put(api + "/api/tuners/" + tuner["id"], json=tuner)
            response.raise_for_status()

        tuners = await get("/api/tuners")
        original = next((t for t in tuners if t["id"] == args.tuner_id), None)
        if original is None:
            raise ProbeError("Tuner ID not found")
        tuner = Tuner.model_validate(original)
        if tuner.control.type != "http_agent":
            raise ProbeError("The test tuner must use the HTTP Agent")
        status = next(t for t in (await get("/api/status"))["tuners"] if t["id"] == tuner.id)
        if status["locked"]:
            raise ProbeError("Tuner is in use; nothing was changed")
        backend = HttpAgentBackend(tuner)
        disabled = False
        launch_attempted = False
        safe_to_restore = True
        channel_key = ""
        try:
            caps = await backend.get_live_capabilities()
            if not caps.get("typed_intent_extras") or not caps.get("playback_state"):
                raise ProbeError("Update Agent and grant Notification Access before probing")
            if PLAYER_PACKAGE not in (await backend.get_info()).packages:
                raise ProbeError("Install FastChannels Player before probing")
            if args.source_channel:
                prepared = [await prepare_external(client, args.fastchannels_url, key,
                                                   os.environ.get("FASTCHANNELS_TOKEN", ""))
                            for key in args.source_channel]
            else:
                prepared = [await prepare_preview(client, args.fastchannels_url, cid) for cid in args.channel_id]
            await put({**original, "enabled": False})
            disabled = True
            # Older APITuner versions do not expose atomic maintenance leases.
            # Recheck after disabling and refuse to disrupt any racing stream.
            status = next(t for t in (await get("/api/status"))["tuners"] if t["id"] == tuner.id)
            if status["locked"]:
                raise ProbeError("A tune raced reservation; aborting the probe")
            print(f"Reserved {tuner.name}; all playback commands use HTTP", flush=True)
            for index, playback in enumerate(prepared, 1):
                channel_key = playback.channel_key
                launch_attempted = True
                safe_to_restore = False
                await backend.launch(package=PLAYER_PACKAGE, component=PLAYER_COMPONENT,
                                     intent_extras=playback.extras)
                # Package-level PLAYING cannot prove a same-app switch completed.
                # A pause plus encoder snapshots provide human-verifiable evidence.
                await asyncio.sleep(2)
                await wait_playing(backend, args.timeout)
                print(f"PLAYING: {playback.title}; DRM requested: {playback.extras['drm']}", flush=True)
                if args.snapshots:
                    args.snapshots.mkdir(parents=True, exist_ok=True)
                    response = await client.get(api + f"/api/tuners/{tuner.id}/preview.jpg")
                    response.raise_for_status()
                    if not response.headers.get("content-type", "").startswith("image/jpeg"):
                        raise ProbeError("Encoder snapshot was not a JPEG")
                    path = args.snapshots / f"channel-{index}.jpg"
                    path.write_bytes(response.content)
                    print(f"Encoder snapshot: {path.resolve()}", flush=True)
                await asyncio.sleep(args.seconds)
                status = await backend.playback_details()
                if status.get("package") != PLAYER_PACKAGE or status.get("state") != "playing":
                    raise ProbeError("Player was not playing at the end of the observation period")
            print("Playback probe completed; this does not qualify long recordings or session isolation", flush=True)
        finally:
            try:
                if launch_attempted:
                    await stop_player(backend, channel_key)
                    safe_to_restore = True
                    print("HTTP warm_stop confirmed; HOME sent", flush=True)
            finally:
                if disabled and safe_to_restore:
                    current = next((t for t in await get("/api/tuners") if t["id"] == tuner.id), None)
                    if current is not None and not current["enabled"]:
                        await put({**current, "enabled": original["enabled"]})
                    print("Restored original tuner availability", flush=True)
                elif disabled:
                    print("Tuner remains disabled because cleanup was not confirmed. Check the device before re-enabling it.", file=sys.stderr)
                await backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apituner-url", required=True)
    parser.add_argument("--fastchannels-url", required=True)
    parser.add_argument("--tuner-id", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--channel-id", type=int, action="append", help="Existing admin preview API (probe only)")
    source.add_argument("--source-channel", action="append", help="source:channel_id via companion API; token from FASTCHANNELS_TOKEN")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--snapshots", type=Path)
    args = parser.parse_args()
    if not (0 <= args.seconds <= 3600 and 1 <= args.timeout <= 180):
        parser.error("seconds must be 0–3600; timeout must be 1–180")
    try:
        asyncio.run(run(args))
    except (ProbeError, httpx.HTTPError) as exc:
        # HTTP exception text may contain a signed URL. Only ProbeError is safe.
        print(str(exc) if isinstance(exc, ProbeError) else f"HTTP request failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
