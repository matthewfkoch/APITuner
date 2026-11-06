"""Authenticated FastChannels catalog client. Never invokes device control."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .fastchannels_probe import ProbeError, http_url


class FastChannelsError(Exception):
    """Safe to display: excludes credentials, provider URLs and response bodies."""


@dataclass(frozen=True)
class CatalogChannel:
    source: str
    channel_id: str
    name: str
    tvg_id: str | None = None
    gracenote_id: str | None = None
    logo_url: str | None = None
    group: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.source, self.channel_id


def parse_catalog(data: object) -> list[CatalogChannel]:
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise FastChannelsError('Unsupported FastChannels catalog version')
    rows = data.get('channels')
    if not isinstance(rows, list) or len(rows) > 50000:
        raise FastChannelsError('Invalid FastChannels channel catalog')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise FastChannelsError('Invalid FastChannels channel entry')
        for field in ('source', 'channel_id', 'name'):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise FastChannelsError('Invalid FastChannels channel identity')
        values = {field: row[field] for field in ('source', 'channel_id', 'name')}
        for field in ('tvg_id', 'gracenote_id', 'logo_url', 'group'):
            value = row.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 4096):
                raise FastChannelsError('Invalid FastChannels channel metadata')
            values[field] = value or None
        channel = CatalogChannel(**values)
        if channel.key in seen:
            raise FastChannelsError('FastChannels returned duplicate channel identities')
        seen.add(channel.key)
        result.append(channel)
    return result


async def fetch_catalog(client: httpx.AsyncClient, base_url: str, token: str) -> list[CatalogChannel]:
    if not token or not token.strip():
        raise FastChannelsError('Configure the FastChannels service token')
    try:
        base = http_url(base_url).rstrip('/')
    except ProbeError:
        raise FastChannelsError('Configure a valid FastChannels HTTP URL') from None
    try:
        response = await client.get(
            base + '/api/external-player/v1/channels',
            headers={'Authorization': 'Bearer ' + token},
            timeout=20, follow_redirects=False,
        )
        if response.status_code in (401, 403):
            raise FastChannelsError('FastChannels rejected the service token')
        if response.status_code == 503:
            raise FastChannelsError('FastChannels external-player API is disabled or unavailable')
        if response.status_code != 200:
            raise FastChannelsError(f'FastChannels catalog request failed (HTTP {response.status_code})')
        if len(response.content) > 16 * 1024 * 1024:
            raise FastChannelsError('FastChannels catalog response is too large')
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise FastChannelsError('Could not read the FastChannels catalog') from None
    return parse_catalog(data)


def service_token() -> str:
    import os
    return os.environ.get('FASTCHANNELS_TOKEN', '').strip()


def merge_catalog(existing, rows: list[CatalogChannel], start: int):
    """Keep stable IDs and user edits, retain missing entries as unavailable."""
    import uuid
    import json
    from .models import Channel
    from .channel_numbers import occupied_integer_numbers
    result = [ch.model_copy(deep=True) for ch in existing]
    known = {(ch.fastchannels_source, ch.fastchannels_channel_id): ch
             for ch in result if ch.source == 'fastchannels'}
    taken = occupied_integer_numbers([ch.number for ch in result])
    present = {row.key for row in rows}
    for key, ch in known.items():
        ch.available = key in present
    number, added = start, 0
    for row in sorted(rows, key=lambda row: row.key):
        if row.key in known:
            continue
        while number in taken:
            number += 1
        identity = uuid.uuid5(uuid.NAMESPACE_URL, 'apituner:fastchannels:' + json.dumps(row.key)).hex
        result.append(Channel(id=identity, number=str(number), name=row.name,
                              source='fastchannels', provider_name=row.source,
                              fastchannels_source=row.source, fastchannels_channel_id=row.channel_id,
                              package_name='com.fastchannels.player', component='com.fastchannels.player/.PlaybackActivity',
                              tvg_id=row.tvg_id, tvc_guide_stationid=row.gracenote_id))
        taken.add(number)
        added += 1
    return result, added


async def sync_fastchannels(store) -> dict:
    import asyncio
    lock = getattr(store, "_fastchannels_sync_lock", None)
    if lock is None:
        lock = store._fastchannels_sync_lock = asyncio.Lock()
    async with lock:
        return await _sync_fastchannels(store)


async def _sync_fastchannels(store) -> dict:
    base = (store.config.options.fastchannels_url or '').strip()
    if not base:
        raise FastChannelsError('Set and save the FastChannels URL first')
    async with httpx.AsyncClient() as client:
        rows = await fetch_catalog(client, base, service_token())
    # No await while committing: preserve edits made during the network request.
    with store._lock:
        if (store.config.options.fastchannels_url or '').strip() != base:
            raise FastChannelsError('FastChannels URL changed during refresh; sync again')
        merged, added = merge_catalog(store.config.channels, rows, store.config.options.fastchannels_start_number)
        from .channels import validate_unique_ids
        validate_unique_ids(merged)
        previous = store.config.channels
        store.config.channels = merged
        try:
            store.save()
        except Exception:
            store.config.channels = previous
            raise FastChannelsError('Could not save the FastChannels lineup') from None
    return dict(available=len(rows), added=added,
                unavailable=sum(ch.source == 'fastchannels' and not ch.available for ch in merged))


async def guide_programmes(base_url, channels):
    """Map upstream XMLTV IDs onto stable APITuner IDs without name matching."""
    import copy
    from xml.etree import ElementTree as ET
    token = service_token()
    if not token:
        raise FastChannelsError('Configure the FastChannels service token')
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(http_url(base_url).rstrip('/') + '/api/external-player/v1/xmltv',
                                        headers={'Authorization': 'Bearer ' + token},
                                        timeout=45, follow_redirects=False)
        if response.status_code != 200 or len(response.content) > 32 * 1024 * 1024:
            raise FastChannelsError('FastChannels guide unavailable')
        if b'<!ENTITY' in response.content.upper():
            raise FastChannelsError('Unsupported FastChannels XMLTV entities')
        root = ET.fromstring(response.content)
        if root.tag != 'tv':
            raise FastChannelsError('Invalid FastChannels XMLTV document')
    except (httpx.HTTPError, ET.ParseError, ProbeError):
        raise FastChannelsError('Could not read FastChannels XMLTV') from None
    mapping = {}
    for ch in channels:
        if ch.source == 'fastchannels' and ch.available and ch.tvg_id:
            mapping.setdefault(ch.tvg_id, []).append(ch.id)
    result = []
    seen = set()
    for programme in root.findall('programme'):
        for identity in mapping.get(programme.get('channel'), []):
            key = (identity, programme.get('start'), programme.get('stop'))
            if key in seen:
                continue
            seen.add(key)
            node = copy.deepcopy(programme)
            node.set('channel', identity)
            result.append(ET.tostring(node, encoding='unicode'))
    return '\n'.join(result)
