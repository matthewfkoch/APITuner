"""APITuner FastAPI application: lifespan, background sync loops, and router wiring."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .adb_grant import grant_agent_permissions  # noqa: F401 - patchable API hook
from .agent_update import download_apk, latest_cache  # noqa: F401 - patchable API hook
from .auto_pair import auto_pair  # noqa: F401 - patchable API hook
from .channels import validate_unique_ids  # noqa: F401 - patchable API hook
from .config import ConfigStore
from .fastchannels import FastChannelsError, sync_fastchannels
from .fruitdeeplinks import sync_fruitdeeplinks
from .hdhr.discovery import DiscoverIdentity, HdhrDiscoveryService
from .hdhr.routes import router as hdhr_router
from .log_buffer import install_log_buffer
from .routes.channels import router as channels_router
from .routes.system import router as system_router
from .routes.tuners import router as tuners_router
from .tuner_manager import TunerManager

logging.basicConfig(
    level=os.environ.get("APITUNER_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
install_log_buffer()
logger = logging.getLogger("apituner")

WEB_DIR = Path(__file__).parent / "web"
AGENT_APK_RELEASES_URL = os.environ.get(
    "APITUNER_AGENT_APK_URL",
    "https://github.com/matthewfkoch/APITuner-releases/releases",
)


def _http_port() -> int:
    return int(os.environ.get("APITUNER_PORT", "6592"))


def _discovery_identity(store: ConfigStore, manager: TunerManager) -> DiscoverIdentity:
    options = store.config.options
    port = options.hdhr_port or _http_port()
    # Discovery replies resolve the LAN IP in HdhrDiscoveryService; seed BaseURL
    # with localhost so HTTP clients still get a usable absolute URL shape.
    base = f"http://127.0.0.1:{port}"
    return DiscoverIdentity(
        device_id_hex=options.hdhr_device_id,
        tuner_count=manager.tuner_count(),
        base_url=base,
        friendly_name=options.hdhr_friendly_name,
    )


async def _fastchannels_sync_loop(store: ConfigStore) -> None:
    while True:
        seconds = store.config.options.fastchannels_sync_seconds
        if seconds and store.config.options.fastchannels_url:
            try:
                await sync_fastchannels(store)
            except FastChannelsError as exc:
                logger.warning("FastChannels refresh failed: %s", exc)
        await asyncio.sleep(max(10, seconds or 10))


async def _fruitdeeplinks_sync_loop(store: ConfigStore) -> None:
    """Optional background refresh of FruitDeepLinks ADB lanes."""
    while True:
        options = store.config.options
        seconds = float(options.fruitdeeplinks_sync_seconds or 0)
        url = (options.fruitdeeplinks_url or "").strip()
        if seconds > 0 and url:
            try:
                await sync_fruitdeeplinks(store)
            except Exception:  # noqa: BLE001
                logger.exception("FruitDeepLinks background sync failed")
            await asyncio.sleep(max(30.0, seconds))
        else:
            await asyncio.sleep(30.0)


async def _warm_tuner_info(store: ConfigStore, manager: TunerManager) -> None:
    """Best-effort concurrent warmup so startup never blocks on offline tuners."""
    enabled = [t.id for t in store.config.tuners if t.enabled]
    if not enabled:
        return

    async def _one(tuner_id: str) -> None:
        try:
            await manager.refresh_info(tuner_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    await asyncio.gather(*(_one(tid) for tid in enabled))


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ConfigStore()
    manager = TunerManager(store)
    app.state.store = store
    app.state.manager = manager
    app.state.hdhr_discovery = None
    await manager.start_reaper()
    warm_task = asyncio.create_task(_warm_tuner_info(store, manager))
    app.state.warm_task = warm_task

    options = store.config.options
    if options.hdhr_enabled and (
        options.hdhr_ssdp_enabled or options.hdhr_udp_discovery_enabled
    ):
        discovery = HdhrDiscoveryService(
            lambda: _discovery_identity(store, manager),
            ssdp_enabled=options.hdhr_ssdp_enabled,
            udp_enabled=options.hdhr_udp_discovery_enabled,
            http_port=options.hdhr_port or _http_port(),
        )
        app.state.hdhr_discovery = discovery
        try:
            await discovery.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("HDHR discovery failed to start: %s", exc)

    logger.info("APITuner %s started", __version__)
    sync_task = asyncio.create_task(_fruitdeeplinks_sync_loop(store))
    app.state.fdl_sync_task = sync_task
    fc_sync_task = asyncio.create_task(_fastchannels_sync_loop(store))
    try:
        yield
    finally:
        for bg in (warm_task, fc_sync_task, sync_task):
            bg.cancel()
            try:
                await bg
            except asyncio.CancelledError:
                pass
        discovery = getattr(app.state, "hdhr_discovery", None)
        if discovery is not None:
            await discovery.stop()
        await manager.stop_reaper()


app = FastAPI(title="APITuner", version=__version__, lifespan=lifespan)
app.include_router(hdhr_router)
app.include_router(system_router)
app.include_router(tuners_router)
app.include_router(channels_router)

# Static assets (css/js). Mounted last so explicit routes win.
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
