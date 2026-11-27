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
async def test_large_xmltv_stream_imports_past_old_limit(monkeypatch):
    import apituner.fastchannels as fc
    from apituner.models import Channel

    class Guide(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'<?xml version="1.0"?><!DOCTYPE tv SYSTEM "xmltv.dtd"><tv>'
            comment = b'<!--' + b'x' * (64 * 1024 - 7) + b'-->'
            for _ in range(33 * 16):
                yield comment
            programme = b'<programme channel="abc.us" start="1" stop="2"><title>News</title></programme>'
            yield programme + programme + b'</tv>'

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Guide()))
    monkeypatch.setattr(fc.httpx, 'AsyncClient', lambda: real_client(transport=transport))
    channels = [
        Channel(package_name='com.test', id='local', number='1', name='News',
                source='fastchannels', tvg_id='abc.us'),
    ]
    result = await fc.guide_programmes('http://fc', channels, token='guide-token')
    assert result == '<programme channel="local" start="1" stop="2"><title>News</title></programme>'
    monkeypatch.setattr(fc, 'MAX_XMLTV_BYTES', 64 * 1024)
    with pytest.raises(FastChannelsError, match='guide exceeds'):
        await fc.guide_programmes('http://fc', channels, token='guide-token')


@pytest.mark.asyncio
async def test_xmltv_rejects_entity_declarations_in_utf16(monkeypatch):
    import apituner.fastchannels as fc

    guide = '<!DOCTYPE tv [<!ENTITY name "expanded">]><tv>&name;</tv>'.encode('utf-16')
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=guide))
    monkeypatch.setattr(fc.httpx, 'AsyncClient', lambda: real_client(transport=transport))
    with pytest.raises(FastChannelsError, match='Unsupported FastChannels XMLTV entities'):
        await fc.guide_programmes('http://fc', [], token='guide-token')


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


def test_connect_import_persists_credential_without_exposing_it(tmp_path, monkeypatch):
    import apituner.fastchannels as fc
    from apituner.config import ConfigStore
    from apituner.main import app
    from apituner.models import GlobalOptions
    from fastapi.testclient import TestClient

    monkeypatch.setenv('APITUNER_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('FASTCHANNELS_TOKEN', raising=False)
    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(hdhr_ssdp_enabled=False, hdhr_udp_discovery_enabled=False)
    store.save()
    real_client = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer saved-credential'
        if request.url.path.endswith('/capabilities'):
            return httpx.Response(200, json={
                'version': 1, 'controls_devices': False, 'package': 'com.fastchannels.player',
                'capabilities': ['catalog', 'prepare', 'xmltv'],
            })
        if request.url.path.endswith('/xmltv'):
            return httpx.Response(200, text='<tv><programme channel="abc.us" start="1" stop="2"/></tv>')
        assert request.url.path.endswith('/channels')
        return httpx.Response(200, json={'version': 1, 'channels': [ROW]})

    monkeypatch.setattr(fc.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(respond)))
    with TestClient(app) as client:
        response = client.post('/api/fastchannels/connect', json={
            'url': 'http://fc:5523/', 'credential': 'saved-credential',
        })
        assert response.status_code == 200, response.text
        assert response.json() == {'available': 1, 'added': 1, 'unavailable': 0}
        saved = ConfigStore(data_dir=tmp_path)
        assert saved.config.options.fastchannels_url == 'http://fc:5523'
        assert fc.service_token(saved.config.options) == 'saved-credential'
        assert saved.path.stat().st_mode & 0o077 == 0
        channel = client.app.state.store.config.channels[0]
        channel.number = '42'
        channel.tvc_guide_stationid = '12345'
        options = client.get('/api/options').json()
        assert 'fastchannels_token' not in options
        options.pop('fastchannels_url')
        assert client.put('/api/options', json=options).status_code == 200
        assert client.post('/api/fastchannels/sync').json()['added'] == 0
        assert client.post('/api/fastchannels/connect', json={'url': 'http://fc:5523'}).json()['added'] == 0
        assert 'saved-credential' not in client.get('/api/status').text
        assert 'saved-credential' not in client.get('/api/diagnostics').text
        guide = client.get('/xmltv.xml')
        assert guide.status_code == 200
        assert f'<programme channel="{channel.id}"' in guide.text
        channel = ConfigStore(data_dir=tmp_path).config.channels[0]
        assert (channel.number, channel.tvc_guide_stationid) == ('42', '12345')
        before = len(calls)
        assert client.post('/api/fastchannels/connect', json={'url': 'http://other'}).status_code == 400
        assert len(calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(('outcome', 'message'), [
    (401, 'rejected the credential'),
    (404, 'does not expose'),
    (None, 'Could not reach'),
    (200, 'Unsupported FastChannels catalog version'),
])
async def test_failed_connect_preserves_configuration(tmp_path, monkeypatch, outcome, message):
    import apituner.fastchannels as fc
    from apituner.config import ConfigStore
    from apituner.models import GlobalOptions

    store = ConfigStore(data_dir=tmp_path)
    store.config.options = GlobalOptions(fastchannels_url='http://original', fastchannels_token='original-token')
    store.config.channels, _ = fc.merge_catalog([], parse_catalog({'version': 1, 'channels': [ROW]}), 10000)
    store.save()
    before = store.path.read_bytes()
    real_client = httpx.AsyncClient

    def respond(request):
        if outcome is None:
            raise httpx.ConnectError('offline')
        if outcome == 200 and request.url.path.endswith('/capabilities'):
            return httpx.Response(200, json={
                'version': 1, 'controls_devices': False, 'package': 'com.fastchannels.player',
                'capabilities': ['catalog', 'prepare', 'xmltv'],
            })
        return httpx.Response(outcome, json={'error': 'private-body'})

    monkeypatch.setattr(fc.httpx, 'AsyncClient', lambda: real_client(transport=httpx.MockTransport(respond)))
    with pytest.raises(FastChannelsError, match=message) as exc:
        await fc.sync_fastchannels(store, url='http://new', credential='new-token')
    assert 'private-body' not in str(exc.value)
    assert store.path.read_bytes() == before
    assert store.config.options.fastchannels_url == 'http://original'
    assert fc.service_token(store.config.options) == 'original-token'
