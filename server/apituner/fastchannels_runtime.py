"""HTTP-only FastChannels tuning with per-lease Player stop ownership."""
import asyncio
import httpx
from .fastchannels import service_token
from .fastchannels_probe import PLAYER_PACKAGE, PLAYER_COMPONENT, prepare_external, wait_playing, stop_player, ProbeError

PLAYER_LOST_GRACE_SECONDS = 15
# The Player can spend up to 120 seconds rejoining the live edge during ad gaps.
PLAYER_RECOVERY_GRACE_SECONDS = 130


async def tune(backend, channel, options, session_key):
    caps = await backend.get_live_capabilities()
    if not caps.get('typed_intent_extras') or not caps.get('playback_state'):
        raise ProbeError('FastChannels requires the updated Agent and Notification Access')
    if PLAYER_PACKAGE not in (await backend.get_info()).packages:
        raise ProbeError('Install FastChannels Player on this tuner')
    async with httpx.AsyncClient() as client:
        request = await prepare_external(client, options.fastchannels_url or '',
                                         channel.fastchannels_source + ':' + channel.fastchannels_channel_id,
                                         service_token(options))
    # Flush previous MediaSession state before accepting a new PLAYING signal.
    # The tuner is exclusively reserved throughout this transition.
    await stop_player(backend, '')
    extras = {**request.extras, 'channel_key': session_key}
    await backend.launch(package=PLAYER_PACKAGE, component=PLAYER_COMPONENT, intent_extras=extras)
    await wait_playing(backend, options.tune_timeout_seconds)


async def monitor(backend, ready_task):
    await asyncio.shield(ready_task)
    recovery_since = None
    lost_since = None
    while True:
        await asyncio.sleep(2)
        try:
            state = await backend.playback_details()
        except Exception:
            state = None
        if not isinstance(state, dict):
            state = {}
        can_read_playback = bool(state) and state.get('hasPermission') is not False
        foreground = None
        if can_read_playback:
            try:
                foreground = await backend.current_app()
            except Exception:
                pass  # Usage Access is optional; fall back to MediaSession.

        session_is_player = state.get('package') == PLAYER_PACKAGE
        playback = state.get('state')
        player_present = can_read_playback and foreground in (None, PLAYER_PACKAGE)
        playing = player_present and session_is_player and playback == 'playing'
        recovering = (player_present and (foreground == PLAYER_PACKAGE or session_is_player)
                      and not (session_is_player and playback in ('stopped', 'error')))

        now = asyncio.get_running_loop().time()
        if playing:
            recovery_since = lost_since = None
            continue
        if recovery_since is None:
            recovery_since = now
        if recovering:
            lost_since = None
        elif lost_since is None:
            lost_since = now
        elif now - lost_since >= PLAYER_LOST_GRACE_SECONDS:
            raise ProbeError('FastChannels Player stopped responding or left playback')
        if now - recovery_since >= PLAYER_RECOVERY_GRACE_SECONDS:
            raise ProbeError('FastChannels Player did not recover playback')
