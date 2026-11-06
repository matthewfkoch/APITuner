"""Playback health and per-session launch ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from apituner import fastchannels_runtime as runtime
from apituner.fastchannels_probe import ProbeError, PLAYER_PACKAGE


@pytest.mark.asyncio
async def test_launch_replaces_provider_key_with_lease_key(monkeypatch):
    backend = AsyncMock()
    backend.get_live_capabilities.return_value = {'typed_intent_extras': True, 'playback_state': True}
    backend.get_info.return_value = SimpleNamespace(packages=[PLAYER_PACKAGE])
    prepare = AsyncMock(return_value=SimpleNamespace(extras={'channel_key': 'sling:abc', 'drm': True}))
    stop, ready = AsyncMock(), AsyncMock()
    monkeypatch.setattr(runtime, 'prepare_external', prepare)
    monkeypatch.setattr(runtime, 'stop_player', stop)
    monkeypatch.setattr(runtime, 'wait_playing', ready)
    await runtime.tune(backend, SimpleNamespace(fastchannels_source='sling', fastchannels_channel_id='abc'),
                       SimpleNamespace(fastchannels_url='http://fc', tune_timeout_seconds=30), 'unique-session')
    assert backend.launch.call_args.kwargs['intent_extras'] == {'channel_key': 'unique-session', 'drm': True}
    stop.assert_awaited_once_with(backend, '')
    ready.assert_awaited_once_with(backend, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['buffering', 'paused', 'none'])
async def test_stalled_playback_fails_within_grace(monkeypatch, state):
    backend = AsyncMock()
    backend.playback_details.return_value = {'package': PLAYER_PACKAGE, 'state': state}
    ready = asyncio.create_task(asyncio.sleep(0))
    await ready
    clock = iter([0, 10, 20])
    loop = SimpleNamespace(time=lambda: next(clock))
    monkeypatch.setattr(runtime, 'asyncio', SimpleNamespace(
        shield=asyncio.shield, sleep=AsyncMock(), get_running_loop=lambda: loop))
    with pytest.raises(ProbeError, match='stopped responding'):
        await runtime.monitor(backend, ready)
