"""Lease ownership survives slow cleanup, disconnects and reaping."""
import asyncio
import time
from unittest.mock import AsyncMock
import pytest
from apituner.config import ConfigStore
from apituner.models import Channel, ControlConfig, GlobalOptions, Tuner
from apituner.tuner_manager import Lease, TunerManager

@pytest.fixture
def leased(tmp_path):
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(stop_on_release=True)
    tuner = Tuner(id='t1', name='Test', control=ControlConfig(type='http_agent', host='192.0.2.1'), stream_endpoint='http://192.0.2.2/stream')
    channel = Channel(number='1', name='Test', package_name='com.test')
    store.config.tuners = [tuner]
    manager = TunerManager(store)
    lease = Lease(tuner=tuner, backend=AsyncMock(), tune_id='owned', channel=channel)
    st = manager._state('t1')
    st.locked, st.tune_id, st.last_seen = True, lease.tune_id, time.time()
    manager._leases[lease.tune_id] = lease
    return manager, lease

@pytest.mark.asyncio
async def test_slow_cleanup_blocks_reallocation_and_duplicate_stop(leased):
    manager, lease = leased
    entered, finish = asyncio.Event(), asyncio.Event()
    async def stop():
        entered.set()
        await finish.wait()
    lease.backend.stop.side_effect = stop
    first = asyncio.create_task(manager.release(lease))
    await entered.wait()
    assert await manager._select(lease.channel, set()) is None
    second = asyncio.create_task(manager.release(lease))
    finish.set()
    await asyncio.gather(first, second)
    lease.backend.stop.assert_awaited_once()
    assert not manager._state('t1').locked
    assert not manager._leases

@pytest.mark.asyncio
async def test_old_release_cannot_stop_new_tune(leased):
    manager, lease = leased
    manager._state('t1').tune_id = 'new'
    await manager.release(lease)
    lease.backend.stop.assert_not_awaited()
    assert manager._state('t1').locked
    assert manager._state('t1').tune_id == 'new'

@pytest.mark.asyncio
async def test_client_cancellation_preserves_cleanup(leased):
    manager, lease = leased
    entered, finish = asyncio.Event(), asyncio.Event()
    async def stop():
        entered.set()
        await finish.wait()
    lease.backend.stop.side_effect = stop
    request = asyncio.create_task(manager.release(lease))
    await entered.wait()
    cleanup = manager._release_tasks[lease.tune_id]
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not cleanup.done()
    assert manager._state('t1').locked
    finish.set()
    await cleanup
    assert not manager._state('t1').locked

@pytest.mark.asyncio
async def test_failed_cleanup_reserves_until_successful_retry(leased):
    manager, lease = leased
    lease.backend.stop.side_effect = RuntimeError('private details')
    await manager.release(lease)
    assert manager._state('t1').locked
    assert 'private details' not in manager._state('t1').last_error
    assert await manager._select(lease.channel, set()) is None
    lease.backend.stop.side_effect = None
    await manager.release(lease)
    assert not manager._state('t1').locked

@pytest.mark.asyncio
async def test_timed_out_cleanup_stays_reserved(leased, monkeypatch):
    manager, lease = leased
    monkeypatch.setattr('apituner.tuner_manager._RELEASE_TIMEOUT_SECONDS', 0.01)
    async def stop():
        await asyncio.Event().wait()
    lease.backend.stop.side_effect = stop
    await asyncio.wait_for(manager.release(lease), 1)
    assert manager._state('t1').locked
    assert 'TimeoutError' in manager._state('t1').last_error
    assert not manager._release_tasks

@pytest.mark.asyncio
async def test_reaper_cleans_before_unlocking(leased):
    manager, lease = leased
    manager._state('t1').last_seen = time.time() - 1000
    await manager._reap_once()
    assert not manager._state('t1').locked
    await manager.release(lease)
    lease.backend.stop.assert_awaited_once()

@pytest.mark.asyncio
async def test_shutdown_cleans_before_closing(leased):
    manager, lease = leased
    manager._backends['t1'] = lease.backend
    async def close():
        assert not manager._state('t1').locked
    lease.backend.close.side_effect = close
    await manager.stop_reaper()
    lease.backend.stop.assert_awaited_once()
    lease.backend.close.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize('tuner_index', [None, 0])
async def test_cancelled_acquisition_stops_and_unlocks(leased, tuner_index):
    manager, previous = leased
    manager._unlock('t1')
    manager._leases.clear()
    manager._backends['t1'] = previous.backend
    manager.options.stop_on_release = False
    manager.options.keep_apps_running = True
    entered = asyncio.Event()
    async def tuning(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    manager._lease_on = tuning
    request = asyncio.create_task(manager.lease(previous.channel, tuner_index=tuner_index))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    previous.backend.stop.assert_awaited_once()
    assert not manager._state('t1').locked
    assert not manager._leases

@pytest.mark.asyncio
async def test_cleanup_deadline_during_tune_cancellation_keeps_reservation(leased, monkeypatch):
    manager, lease = leased
    monkeypatch.setattr('apituner.tuner_manager._RELEASE_TIMEOUT_SECONDS', 0.01)
    started = asyncio.Event()
    async def tune():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.Event().wait()
    lease.tune_task = asyncio.create_task(tune())
    await started.wait()
    await asyncio.wait_for(manager.release(lease), 1)
    assert manager._state('t1').locked
    assert 'TimeoutError' in manager._state('t1').last_error
    lease.backend.stop.assert_not_awaited()
    assert lease.tune_task.done()
