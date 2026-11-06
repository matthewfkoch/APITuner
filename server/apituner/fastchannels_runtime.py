"""HTTP-only FastChannels tuning with per-lease Player stop ownership."""
import asyncio
import httpx
from .fastchannels import service_token
from .fastchannels_probe import PLAYER_PACKAGE, PLAYER_COMPONENT, prepare_external, wait_playing, stop_player, ProbeError


async def tune(backend, channel, options, session_key):
    caps = await backend.get_live_capabilities()
    if not caps.get('typed_intent_extras') or not caps.get('playback_state'):
        raise ProbeError('FastChannels requires the updated Agent and Notification Access')
    if PLAYER_PACKAGE not in (await backend.get_info()).packages:
        raise ProbeError('Install FastChannels Player on this tuner')
    async with httpx.AsyncClient() as client:
        request = await prepare_external(client, options.fastchannels_url or '',
                                         channel.fastchannels_source + ':' + channel.fastchannels_channel_id,
                                         service_token())
    # Flush previous MediaSession state before accepting a new PLAYING signal.
    # The tuner is exclusively reserved throughout this transition.
    await stop_player(backend, '')
    extras = {**request.extras, 'channel_key': session_key}
    await backend.launch(package=PLAYER_PACKAGE, component=PLAYER_COMPONENT, intent_extras=extras)
    await wait_playing(backend, options.tune_timeout_seconds)


async def monitor(backend, ready_task):
    await asyncio.shield(ready_task)
    bad_since = None
    while True:
        await asyncio.sleep(2)
        try:
            state = await backend.playback_details()
            healthy = (state.get('hasPermission') is not False
                       and state.get('package') == PLAYER_PACKAGE
                       and state.get('state') == 'playing')
        except Exception:
            healthy = False
        now = asyncio.get_running_loop().time()
        if healthy:
            bad_since = None
        elif bad_since is None:
            bad_since = now
        elif now - bad_since >= 15:
            raise ProbeError('FastChannels Player stopped responding or left playback')
