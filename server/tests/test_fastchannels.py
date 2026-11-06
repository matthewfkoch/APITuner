import httpx
import pytest
from apituner.fastchannels import FastChannelsError, fetch_catalog, parse_catalog

ROW = {'source': 'sling', 'channel_id': 'abc', 'name': 'ABC News', 'tvg_id': 'abc.us'}


def test_catalog_identity_and_guide_metadata():
    channel = parse_catalog({'version': 1, 'channels': [ROW]})[0]
    assert channel.key == ('sling', 'abc')
    assert channel.tvg_id == 'abc.us'


@pytest.mark.parametrize('data', [None, {'version': True, 'channels': []}, {'version': 2, 'channels': []}, {'version': 1, 'channels': [ROW, ROW]}, {'version': 1, 'channels': [{**ROW, 'channel_id': ''}]}])
def test_invalid_catalog_rejects_entire_refresh(data):
    with pytest.raises(FastChannelsError):
        parse_catalog(data)


@pytest.mark.asyncio
async def test_catalog_uses_service_token_and_does_not_follow_redirect():
    calls = []
    def respond(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer test-token'
        return httpx.Response(302, headers={'Location': 'http://elsewhere.invalid'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(FastChannelsError, match='HTTP 302'):
            await fetch_catalog(client, 'http://fastchannels', 'test-token')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_catalog_errors_never_echo_response_secrets():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text='private-secret'))) as client:
        with pytest.raises(FastChannelsError) as error:
            await fetch_catalog(client, 'http://fastchannels', 'test-token')
    assert 'private-secret' not in str(error.value)


def test_refresh_preserves_edits_numbers_and_restores_missing_channels():
    from apituner.fastchannels import merge_catalog
    from apituner.models import Channel
    rows = parse_catalog({'version': 1, 'channels': [ROW]})
    original = Channel(package_name='com.test', id='manual', name='Existing', number='10000')
    first, added = merge_catalog([original], rows, 10000)
    assert added == 1 and first[1].number == '10001'
    identity = first[1].id
    first[1].name = 'My custom name'
    first[1].number = '42'
    missing, added = merge_catalog(first, [], 10000)
    assert added == 0 and not missing[1].available
    restored, added = merge_catalog(missing, rows, 10000)
    assert added == 0 and restored[1].available
    assert (restored[1].id, restored[1].name, restored[1].number) == (identity, 'My custom name', '42')
    assert first[1].available  # Merge never mutates the live configuration.


@pytest.mark.asyncio
async def test_guide_maps_only_available_source_channels(monkeypatch):
    import apituner.fastchannels as fc
    from apituner.models import Channel
    real_client = httpx.AsyncClient
    def respond(request):
        assert request.headers['Authorization'] == 'Bearer guide-token'
        assert request.url.path.endswith('/xmltv')
        return httpx.Response(200, text='<tv><programme channel="abc.us" start="1" stop="2"><title>News</title></programme><programme channel="foreign" start="1" stop="2"/></tv>')
    monkeypatch.setenv('FASTCHANNELS_TOKEN', 'guide-token')
    monkeypatch.setattr(fc.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(respond)))
    channels = [Channel(package_name='com.test', id='local', number='1', name='News', source='fastchannels', tvg_id='abc.us'),
                Channel(package_name='com.test', id='gone', number='2', name='Gone', source='fastchannels', tvg_id='abc.us', available=False)]
    result = await fc.guide_programmes('http://fc', channels)
    assert 'channel="local"' in result
    assert 'gone' not in result and 'foreign' not in result and 'abc.us' not in result


@pytest.mark.asyncio
async def test_failed_refresh_keeps_persisted_lineup(tmp_path, monkeypatch):
    import apituner.fastchannels as fc
    from apituner.config import ConfigStore
    from apituner.models import Channel
    from unittest.mock import AsyncMock
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.fastchannels_url = 'http://fc'
    store.config.channels = [Channel(package_name='com.test', id='saved', number='1', name='Saved')]
    store.save()
    before = store.config.model_dump()
    monkeypatch.setattr(fc, 'fetch_catalog', AsyncMock(side_effect=FastChannelsError('offline')))
    with pytest.raises(FastChannelsError, match='offline'):
        await fc.sync_fastchannels(store)
    assert store.config.model_dump() == before


@pytest.mark.asyncio
async def test_sync_persists_stable_source_identity(tmp_path, monkeypatch):
    import apituner.fastchannels as fc
    from apituner.config import ConfigStore
    from unittest.mock import AsyncMock
    store = ConfigStore(data_dir=tmp_path)
    store.config.options.fastchannels_url = 'http://fc'
    monkeypatch.setattr(fc, 'fetch_catalog', AsyncMock(return_value=parse_catalog({'version': 1, 'channels': [ROW]})))
    assert (await fc.sync_fastchannels(store))['added'] == 1
    assert (await fc.sync_fastchannels(store))['added'] == 0
    assert len(store.config.channels) == 1
    assert store.config.channels[0].fastchannels_channel_id == 'abc'
