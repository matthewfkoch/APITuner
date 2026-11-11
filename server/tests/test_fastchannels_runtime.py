"""Playback health and per-session launch ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from apituner import fastchannels_runtime as runtime
from apituner.fastchannels_probe import ProbeError, PLAYER_PACKAGE
from apituner.models import GlobalOptions


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
                       GlobalOptions(fastchannels_url='http://fc', fastchannels_token='player-token',
                                     tune_timeout_seconds=30), 'unique-session')
    assert prepare.await_args.args[-1] == 'player-token'
    assert backend.launch.call_args.kwargs['intent_extras'] == {'channel_key': 'unique-session', 'drm': True}
    stop.assert_awaited_once_with(backend, '')
    ready.assert_awaited_once_with(backend, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(('foreground', 'state', 'times', 'message'), [
    (PLAYER_PACKAGE, 'buffering', [0, 16, 58, 120, 130], 'did not recover'),
    (None, 'paused', [0, 16, 58, 120, 130], 'did not recover'),
    (PLAYER_PACKAGE, 'stopped', [0, 15], 'left playback'),
    ('com.android.launcher', 'playing', [0, 15], 'left playback'),
    (None, None, [0, 15], 'stopped responding'),
])
async def test_playback_recovery_and_loss_have_different_deadlines(monkeypatch, foreground, state, times, message):
    backend = AsyncMock()
    backend.current_app.return_value = foreground
    if state is None:
        backend.playback_details.side_effect = OSError('Agent is unreachable')
    else:
        backend.playback_details.return_value = {'package': PLAYER_PACKAGE, 'state': state}
    ready = asyncio.create_task(asyncio.sleep(0))
    await ready
    clock = iter(times)
    loop = SimpleNamespace(time=lambda: next(clock))
    monkeypatch.setattr(runtime, 'asyncio', SimpleNamespace(
        shield=asyncio.shield, sleep=AsyncMock(), get_running_loop=lambda: loop))
    with pytest.raises(ProbeError, match=message):
        await runtime.monitor(backend, ready)
    assert backend.playback_details.await_count == len(times)


@pytest.mark.asyncio
async def test_recovery_window_resets_and_player_exit_still_fails_quickly(monkeypatch):
    backend = AsyncMock()
    backend.current_app.side_effect = [PLAYER_PACKAGE] * 5 + ['com.android.launcher'] * 2
    backend.playback_details.side_effect = [
        {'package': PLAYER_PACKAGE, 'state': 'buffering'},
        {'package': None, 'state': 'none'},
        {'package': PLAYER_PACKAGE, 'state': 'playing'},
        {'package': PLAYER_PACKAGE, 'state': 'buffering'},
        {'package': PLAYER_PACKAGE, 'state': 'buffering'},
        {'package': PLAYER_PACKAGE, 'state': 'buffering'},
        {'package': PLAYER_PACKAGE, 'state': 'buffering'},
    ]
    ready = asyncio.create_task(asyncio.sleep(0))
    await ready
    clock = iter([0, 58, 60, 75, 135, 140, 155])
    loop = SimpleNamespace(time=lambda: next(clock))
    monkeypatch.setattr(runtime, 'asyncio', SimpleNamespace(
        shield=asyncio.shield, sleep=AsyncMock(), get_running_loop=lambda: loop))
    with pytest.raises(ProbeError, match='left playback'):
        await runtime.monitor(backend, ready)
    assert backend.playback_details.await_count == 7
