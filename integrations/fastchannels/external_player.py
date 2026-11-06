"""Opt-in, resolve-only API for external Android player controllers.

This API never connects to a device, allocates capture hardware, or registers an
ADB watchdog. The external controller owns playback and its tuner lifecycle.
Set FC_EXTERNAL_PLAYER_TOKEN to enable it; browser admin sessions are not used.
"""
from __future__ import annotations

import hmac
import os

from flask import Blueprint, jsonify, request

external_player_bp = Blueprint('external_player', __name__)


@external_player_bp.before_request
def authorize():
    expected = os.environ.get('FC_EXTERNAL_PLAYER_TOKEN', '')
    if not expected:
        return jsonify(error='External player API is disabled'), 503
    actual = request.headers.get('Authorization', '')
    if not hmac.compare_digest(actual.encode(), ('Bearer ' + expected).encode()):
        return jsonify(error='External player token required'), 401


@external_player_bp.after_request
def prevent_caching(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@external_player_bp.get('/capabilities')
def capabilities():
    return jsonify(version=1, capabilities=['catalog', 'prepare', 'xmltv'],
                   controls_devices=False, package='com.fastchannels.player')


def _catalog_channels():
    from ..models import Channel, Source
    from ..drm_bridge import DRM_BRIDGE_TRUSTED_SOURCES
    from sqlalchemy import or_
    # Do not require a legacy ADB bridge configuration to expose candidates.
    # Keep source/channel enablement and retirement policy authoritative.
    from .play import _channel_is_retired
    rows = (Channel.query.join(Source)
            .filter(Source.is_enabled == True, Source.epg_only == False,
                    Channel.review_state == 'approved', Channel.is_enabled == True,
                    Channel.is_active == True,
                    Source.name.in_(DRM_BRIDGE_TRUSTED_SOURCES),
                    or_(Channel.requires_drm_bridge == True, Channel.stream_type == 'dash'))
            .order_by(Source.name, Channel.source_channel_id).all())
    return [ch for ch in rows if not _channel_is_retired(ch)]


def _catalog_row(ch):
    from ..generators.m3u import _tvg_id, _parse_gracenote_id
    return dict(source=ch.source.name, channel_id=ch.source_channel_id,
                name=ch.name, tvg_id=_tvg_id(ch),
                gracenote_id=_parse_gracenote_id(ch),
                logo_url=ch.logo_url, group=ch.category)


@external_player_bp.get('/channels')
def channels():
    return jsonify(version=1, channels=[_catalog_row(ch) for ch in _catalog_channels()])


def _load_channel(source, channel_id):
    from ..models import Channel, Source
    from ..drm_bridge import DRM_BRIDGE_TRUSTED_SOURCES
    if source not in DRM_BRIDGE_TRUSTED_SOURCES:
        return None
    return (Channel.query.join(Source)
            .filter(Source.name == source, Channel.source_channel_id == channel_id,
                    Source.is_enabled == True, Source.epg_only == False,
                    Channel.review_state == 'approved', Channel.is_enabled == True,
                    Channel.is_active == True).first())


def _resolve(ch):
    from ..fc_player_bridge import prepare_player_channel
    return prepare_player_channel(ch)


def _is_retired(channel):
    from .play import _channel_is_retired
    return _channel_is_retired(channel)


@external_player_bp.post('/prepare')
def prepare():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'source', 'channel_id'}:
        return jsonify(error='Expected source and channel_id only'), 400
    if any(not isinstance(body[k], str) or not body[k].strip() or len(body[k]) > 512
           for k in ('source', 'channel_id')):
        return jsonify(error='Invalid channel identity'), 400
    channel = _load_channel(body['source'], body['channel_id'])
    if channel is None:
        return jsonify(error='Channel is unavailable or not player-compatible'), 404
    if _is_retired(channel):
        return jsonify(error='Channel has retired'), 410
    try:
        extras = _resolve(channel)
    except Exception:
        # Resolver errors can include tokens, license URLs, or provider credentials.
        return jsonify(error='Could not prepare channel playback'), 502
    return jsonify(version=1, package='com.fastchannels.player',
                   component='com.fastchannels.player/.PlaybackActivity',
                   intent_extras=extras)


@external_player_bp.get('/xmltv')
def xmltv():
    from flask import Response, stream_with_context
    from ..generators.xmltv import generate_xmltv_stream
    ids = [ch.id for ch in _catalog_channels()]
    if not ids:
        return Response('<?xml version="1.0"?><tv/>', mimetype='application/xml')
    return Response(stream_with_context(generate_xmltv_stream(
        {'channel_ids': ids}, base_url=request.host_url, native=True)), mimetype='application/xml')
