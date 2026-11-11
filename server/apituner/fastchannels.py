"""Authenticated FastChannels catalog client. Never invokes device control."""
from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import DefusedXMLParser
from pydantic import SecretStr

from .fastchannels_probe import PLAYER_PACKAGE, ProbeError, http_url
from .models import GlobalOptions

MAX_XMLTV_BYTES = 128 * 1024 * 1024


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


def connection_url(value: str | None) -> str:
    return (value or '').strip().rstrip('/')


async def _request(client: httpx.AsyncClient, base_url: str, token: str, resource: str) -> httpx.Response:
    try:
        base = http_url(connection_url(base_url))
        parsed = urlsplit(base)
        if parsed.query or parsed.fragment or parsed.path.rstrip('/').endswith('/admin'):
            raise ValueError
    except (ProbeError, ValueError):
        raise FastChannelsError('Enter the FastChannels server URL, such as http://fastchannels:5523.') from None
    try:
        response = await client.get(
            base + '/api/external-player/v1/' + resource,
            headers={'Authorization': 'Bearer ' + token},
            timeout=20, follow_redirects=False,
        )
    except httpx.InvalidURL:
        raise FastChannelsError('Enter a valid FastChannels server URL.') from None
    except (httpx.HTTPError, ValueError):
        raise FastChannelsError('Could not reach FastChannels. Check the server URL from APITuner.') from None
    if response.status_code in (401, 403):
        raise FastChannelsError('FastChannels rejected the credential. Check the external player token.')
    if response.status_code in (404, 405) or response.is_redirect:
        raise FastChannelsError(
            f'This URL does not expose the FastChannels external player API (HTTP {response.status_code}). '
            'Use a compatible FastChannels server and its base URL.'
        )
    if response.status_code == 503:
        raise FastChannelsError('In FastChannels, open Bridge → External tuners and enable Allow external tuners.')
    if response.status_code != 200:
        raise FastChannelsError(f'FastChannels request failed (HTTP {response.status_code}).')
    return response


def _json_response(response: httpx.Response) -> object:
    if len(response.content) > 16 * 1024 * 1024:
        raise FastChannelsError('FastChannels response is too large')
    try:
        return response.json()
    except ValueError:
        raise FastChannelsError('This URL did not return a compatible FastChannels API response.') from None


async def fetch_catalog(client: httpx.AsyncClient, base_url: str, token: str) -> list[CatalogChannel]:
    if not token:
        raise FastChannelsError('Enter the FastChannels external player credential.')
    return parse_catalog(_json_response(await _request(client, base_url, token, 'channels')))


def service_token(options: GlobalOptions | None = None) -> str:
    if options is not None and options.fastchannels_token:
        return options.fastchannels_token.get_secret_value()
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


async def sync_fastchannels(store, *, url: str | None = None, credential: str = '') -> dict:
    """Refresh the saved source, or validate and commit a new connection and lineup."""
    lock = getattr(store, "_fastchannels_sync_lock", None)
    if lock is None:
        lock = store._fastchannels_sync_lock = asyncio.Lock()
    async with lock:
        previous_url = connection_url(store.config.options.fastchannels_url)
        base = previous_url if url is None else connection_url(url)
        if not base:
            raise FastChannelsError('Enter the FastChannels server URL.')
        token = credential or (service_token(store.config.options) if base == previous_url else '')
        async with httpx.AsyncClient() as client:
            if url is not None:
                if not token:
                    raise FastChannelsError('Enter the FastChannels external player credential.')
                caps = _json_response(await _request(client, base, token, 'capabilities'))
                if (not isinstance(caps, dict) or type(caps.get('version')) is not int or caps['version'] != 1
                        or caps.get('package') != PLAYER_PACKAGE or caps.get('controls_devices') is not False
                        or not isinstance(caps.get('capabilities'), list)
                        or not all(name in caps['capabilities'] for name in ('catalog', 'prepare', 'xmltv'))):
                    raise FastChannelsError('This FastChannels server does not support the required external player API.')
            rows = await fetch_catalog(client, base, token)
        # Commit against the latest channel edits and unrelated settings.
        with store._lock:
            options = store.config.options
            if connection_url(options.fastchannels_url) != previous_url:
                raise FastChannelsError('FastChannels URL changed during import. Try again.')
            previous = store.config.channels
            merged, added = merge_catalog(previous, rows, options.fastchannels_start_number)
            from .channels import validate_unique_ids
            validate_unique_ids(merged)
            store.config.channels = merged
            if url is not None:
                store.config.options = options.model_copy(update={
                    'fastchannels_url': base,
                    'fastchannels_token': SecretStr(credential) if credential else options.fastchannels_token,
                })
            try:
                store.save()
            except OSError:
                store.config.options = options
                store.config.channels = previous
                raise FastChannelsError('Could not save the FastChannels connection and lineup.') from None
        return dict(available=len(rows), added=added,
                    unavailable=sum(ch.source == 'fastchannels' and not ch.available for ch in merged))


class _GuideBuilder(ET.TreeBuilder):
    """Remap programmes and discard each source entry as soon as it closes."""

    def __init__(self, mapping):
        super().__init__()
        self.mapping = mapping
        self.depth = 0
        self.root = None
        self.result = []
        self.seen = set()

    def start(self, tag, attrs):
        if self.depth == 0 and tag != 'tv':
            raise FastChannelsError('Invalid FastChannels XMLTV document')
        element = super().start(tag, attrs)
        if self.depth == 0:
            self.root = element
        self.depth += 1
        return element

    def end(self, tag):
        element = super().end(tag)
        self.depth -= 1
        if self.depth == 1:
            if tag == 'programme':
                for identity in self.mapping.get(element.get('channel'), []):
                    key = (identity, element.get('start'), element.get('stop'))
                    if key not in self.seen:
                        self.seen.add(key)
                        node = deepcopy(element)
                        node.set('channel', identity)
                        self.result.append(ET.tostring(node, encoding='unicode'))
            self.root.remove(element)
        return element


async def guide_programmes(base_url, channels, *, token=None):
    """Map upstream XMLTV IDs onto stable APITuner IDs without name matching."""
    if token is None:
        token = service_token()
    if not token:
        raise FastChannelsError('Configure the FastChannels service token')
    mapping = {}
    for ch in channels:
        if ch.source == 'fastchannels' and ch.available and ch.tvg_id:
            mapping.setdefault(ch.tvg_id, []).append(ch.id)
    builder = _GuideBuilder(mapping)
    parser = DefusedXMLParser(target=builder, forbid_entities=True, forbid_external=True)
    size = 0
    try:
        url = http_url(base_url).rstrip('/') + '/api/external-player/v1/xmltv'
        async with httpx.AsyncClient() as client:
            async with client.stream(
                'GET', url,
                headers={'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'},
                timeout=45, follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise FastChannelsError(f'FastChannels guide unavailable (HTTP {response.status_code})')
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    size += len(chunk)
                    if size > MAX_XMLTV_BYTES:
                        raise FastChannelsError('FastChannels XMLTV guide exceeds 128 MiB')
                    parser.feed(chunk)
                parser.close()
    except DefusedXmlException:
        raise FastChannelsError('Unsupported FastChannels XMLTV entities') from None
    except (httpx.HTTPError, httpx.InvalidURL, ET.ParseError, ProbeError, ValueError):
        raise FastChannelsError('Could not read FastChannels XMLTV') from None
    return '\n'.join(builder.result)
