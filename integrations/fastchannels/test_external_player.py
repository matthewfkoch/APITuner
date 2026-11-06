"""Run with pytest and Flask; database/provider calls are replaced with fixtures."""
import importlib.util
from pathlib import Path

import pytest

flask = pytest.importorskip('flask')
spec = importlib.util.spec_from_file_location('external_player_under_test', Path(__file__).with_name('external_player.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('FC_EXTERNAL_PLAYER_TOKEN', 'test-external-player-token')
    app = flask.Flask(__name__)
    app.register_blueprint(module.external_player_bp, url_prefix='/api/external-player/v1')
    return app.test_client()


HEADERS = {'Authorization': 'Bearer test-external-player-token'}
BASE = '/api/external-player/v1'


def test_api_is_disabled_without_explicit_token(client, monkeypatch):
    monkeypatch.delenv('FC_EXTERNAL_PLAYER_TOKEN')
    assert client.get(BASE + '/capabilities', headers=HEADERS).status_code == 503


def test_browser_session_cannot_replace_service_token(client):
    assert client.get(BASE + '/capabilities').status_code == 401
    assert client.get(BASE + '/capabilities', headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_capabilities_never_claim_device_control(client):
    response = client.get(BASE + '/capabilities', headers=HEADERS)
    assert response.json['version'] == 1
    assert response.json['controls_devices'] is False
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('body', [None, [], {}, {'source': 'sling'}, {'source': 1, 'channel_id': 'abc'}, {'source': 'sling', 'channel_id': 'abc', 'adb': '192.0.2.1'}])
def test_prepare_rejects_invalid_or_device_control_fields(client, body):
    response = client.post(BASE + '/prepare', headers=HEADERS, json=body)
    assert response.status_code == 400


def test_prepare_preserves_typed_extras(client, monkeypatch):
    channel = object()
    monkeypatch.setattr(module, '_load_channel', lambda source, cid: channel)
    monkeypatch.setattr(module, '_is_retired', lambda ch: False)
    extras = {'stream_url': 'http://fc/stream?a=b,c', 'drm': True, 'captions': False}
    monkeypatch.setattr(module, '_resolve', lambda ch: extras)
    response = client.post(BASE + '/prepare', headers=HEADERS, json={'source': 'sling', 'channel_id': 'abc'})
    assert response.status_code == 200
    assert response.json['intent_extras'] == extras
    assert response.json['component'] == 'com.fastchannels.player/.PlaybackActivity'
    assert response.headers['Cache-Control'] == 'no-store'


def test_resolver_errors_do_not_expose_tokens(client, monkeypatch):
    monkeypatch.setattr(module, '_load_channel', lambda *args: object())
    monkeypatch.setattr(module, '_is_retired', lambda ch: False)
    def broken(ch):
        raise RuntimeError('http://provider/license?secret=private')
    monkeypatch.setattr(module, '_resolve', broken)
    response = client.post(BASE + '/prepare', headers=HEADERS, json={'source': 'sling', 'channel_id': 'abc'})
    assert response.status_code == 502
    assert b'private' not in response.data


def test_unavailable_channel_does_not_resolve(client, monkeypatch):
    monkeypatch.setattr(module, '_load_channel', lambda *args: None)
    response = client.post(BASE + '/prepare', headers=HEADERS, json={'source': 'sling', 'channel_id': 'missing'})
    assert response.status_code == 404


def test_retired_channel_does_not_resolve(client, monkeypatch):
    monkeypatch.setattr(module, '_load_channel', lambda *args: object())
    monkeypatch.setattr(module, '_is_retired', lambda ch: True)
    response = client.post(BASE + '/prepare', headers=HEADERS, json={'source': 'sling', 'channel_id': 'old'})
    assert response.status_code == 410
