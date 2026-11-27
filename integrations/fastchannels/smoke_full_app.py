"""Run inside the patched image with --network none and an ephemeral /data.

Uses the real application, authentication middleware, ORM and preparation helper.
Only provider resolution is stubbed; no device or provider network calls occur.
"""
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

test_dir = tempfile.TemporaryDirectory(prefix='fc-external-smoke-')
os.environ['DATABASE_URL'] = 'sqlite:///' + test_dir.name + '/test.db'
os.environ['FASTCHANNELS_SECRET_KEY_FILE'] = test_dir.name + '/secret'
os.environ['VERSION_CHECK_ENABLED'] = '0'
os.environ['FC_EXTERNAL_PLAYER_TOKEN'] = secrets.token_hex(32)

from app import create_app
from app.extensions import db
from app.models import Channel, Source, Program

app = create_app()
client = app.test_client()
base = '/api/external-player/v1'
headers = {'Authorization': 'Bearer ' + os.environ['FC_EXTERNAL_PLAYER_TOKEN']}

assert client.get(base + '/capabilities').status_code == 401
assert client.get(base + '/capabilities', headers=headers).json['controls_devices'] is False

with app.app_context():
    source = Source(name='sling', display_name='Smoke fixture', is_enabled=True)
    db.session.add(source)
    db.session.flush()
    for key, overrides in [
        ('approved', {}),
        ('pending', {'review_state': 'pending'}),
        ('disabled', {'is_enabled': False}),
        ('inactive', {'is_active': False}),
        ('retired', {'retire_at': datetime.now(timezone.utc) - timedelta(days=1)}),
    ]:
        values = dict(source_id=source.id, source_channel_id=key, name=key,
                      stream_url='https://example.invalid/test.mpd', stream_type='dash',
                      is_enabled=True, is_active=True, review_state='approved')
        values.update(overrides)
        db.session.add(Channel(**values))
    db.session.commit()

    catalog = client.get(base + '/channels', headers=headers)
    assert catalog.status_code == 200, catalog.status_code
    assert [ch['channel_id'] for ch in catalog.json['channels']] == ['approved']
    assert catalog.headers['Cache-Control'] == 'no-store'

    from xml.etree import ElementTree as ET
    approved = Channel.query.filter_by(source_channel_id='approved').one()
    db.session.add(Program(channel_id=approved.id, title='Guide smoke test',
                           start_time=datetime.now(timezone.utc),
                           end_time=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.session.commit()
    guide = client.get(base + '/xmltv', headers=headers)
    assert guide.status_code == 200
    root = ET.fromstring(guide.data)
    assert len(root.findall('channel')) == 1
    assert root.find('programme/title').text == 'Guide smoke test'
    assert root.find('programme').get('channel') == catalog.json['channels'][0]['tvg_id']
    assert client.get(base + '/xmltv').status_code == 401

    def prepare(key):
        return client.post(base + '/prepare', headers=headers,
                           json={'source': 'sling', 'channel_id': key})

    with patch('app.routes.api_playback._get_playback_info', return_value={
        'preview_url': '/play/sling/test/dash.mpd',
        'license_url': '/play/sling/license?channel_id=test',
    }) as resolver:
        response = prepare('approved')
        assert response.status_code == 200, response.status_code
        extras = response.json['intent_extras']
        assert extras['drm'] is True
        assert extras['channel_key'] == 'sling:approved'
        assert extras['stream_url'] == 'http://localhost/play/sling/test/dash.mpd'
        assert extras['license_url'] == 'http://localhost/play/sling/license?channel_id=test'
        resolver.assert_called_once()
        resolver.reset_mock()
        for key in ('pending', 'disabled', 'inactive', 'missing'):
            assert prepare(key).status_code == 404, key
        assert prepare('retired').status_code == 410
        source.epg_only = True
        db.session.commit()
        assert client.get(base + '/channels', headers=headers).json['channels'] == []
        assert not ET.fromstring(client.get(base + '/xmltv', headers=headers).data).findall('channel')
        assert prepare('approved').status_code == 404
        source.epg_only = False
        source.is_enabled = False
        db.session.commit()
        assert prepare('approved').status_code == 404
        resolver.assert_not_called()

print('PASS: real app auth, ORM catalog policy, typed preparation, and unavailable-channel rejection')
