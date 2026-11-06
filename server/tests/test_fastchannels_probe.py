from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from apituner.fastchannels_probe import (
    PLAYER_PACKAGE, ProbeError, from_preview, prepare_preview, stop_player, wait_playing,
)


def preview(**channel):
    return {
        "channel": {"name": "News: live, 日本語", "source_name": "example",
                    "source_channel_id": "abc:123", **channel},
        "preview_url": "/play/example/abc/proxy.m3u8?x=a,b&token=a:b",
    }


def test_preview_preserves_punctuation_and_typed_parameters():
    result = from_preview("http://fc:5523", preview(license_url="/play/example/license?id=abc"))
    assert result.channel_key == "example:abc:123"
    assert result.extras["stream_url"] == "http://fc:5523/play/example/abc/proxy.m3u8?x=a,b&token=a:b"
    assert result.extras["license_url"] == "http://fc:5523/play/example/license?id=abc"
    assert result.extras["drm"] is True
    assert result.extras["captions"] is False


@pytest.mark.parametrize("url", ["/play/fc-player/example/abc.m3u8", "/play/auto/example/abc.m3u8", "file:///tmp/test", "http://user:password@fc/play", "/play/prismcast/1.ts"])
def test_preview_rejects_capture_triggers_and_unsupported_urls(url):
    data = preview()
    data["preview_url"] = url
    with pytest.raises(ProbeError):
        from_preview("http://fc:5523", data)


def test_protected_channel_requires_license_information():
    with pytest.raises(ProbeError, match="license"):
        from_preview("http://fc", preview(requires_drm_bridge=True))


@pytest.mark.asyncio
async def test_preview_auth_failure_is_clear_and_does_not_follow_redirects():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        with pytest.raises(ProbeError, match="admin login"):
            await prepare_preview(client, "http://fc", 1)


@pytest.mark.asyncio
async def test_readiness_does_not_accept_buffering_or_another_app():
    backend = AsyncMock()
    backend.playback_details.side_effect = [
        {"package": PLAYER_PACKAGE, "playing": True, "state": "buffering"},
        {"package": "other.app", "playing": True, "state": "playing"},
        {"package": PLAYER_PACKAGE, "playing": True, "state": "playing"},
    ]
    result = await wait_playing(backend, 3)
    assert result["state"] == "playing"
    assert backend.playback_details.await_count == 3


@pytest.mark.asyncio
async def test_readiness_requires_permission():
    backend = AsyncMock()
    backend.playback_details.return_value = {"hasPermission": False}
    with pytest.raises(ProbeError, match="Notification"):
        await wait_playing(backend, 1)


@pytest.mark.asyncio
async def test_warm_stop_precedes_home_and_requires_confirmation():
    backend = AsyncMock()
    backend.playback_details.return_value = {"playing": False, "state": "none", "hasPermission": True}
    await stop_player(backend, "example:abc")
    assert backend.launch.call_args.kwargs["intent_extras"] == {"command": "warm_stop", "channel_key": "example:abc"}
    backend.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_playback_does_not_confirm_stop():
    backend = AsyncMock()
    backend.playback_details.return_value = {}
    with pytest.raises(ProbeError, match="confirm Player stop"):
        await stop_player(backend, "example:abc", timeout=0.01)
    backend.stop.assert_not_called()


@pytest.mark.asyncio
async def test_external_contract_sends_token_only_to_prepare_and_filters_commands():
    from apituner.fastchannels_probe import PLAYER_COMPONENT, prepare_external
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer service-token'
        assert request.url.path == '/api/external-player/v1/prepare'
        return httpx.Response(200, json={
            'version': 1, 'package': PLAYER_PACKAGE, 'component': PLAYER_COMPONENT,
            'intent_extras': {'channel_key': 'sling:abc', 'title': 'News',
                             'stream_url': 'http://fc/play/sling/abc/dash.mpd',
                             'license_url': 'http://fc/play/sling/license',
                             'drm': True, 'captions': True, 'audio_mode': 'pcm',
                             'command': 'warm_stop'},
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await prepare_external(client, 'http://fc', 'sling:abc', 'service-token')
    assert result.extras['drm'] is True
    assert result.extras['captions'] is True
    assert result.extras['audio_mode'] == 'pcm'
    assert 'command' not in result.extras


@pytest.mark.asyncio
async def test_external_contract_does_not_follow_redirect_or_fallback():
    from apituner.fastchannels_probe import prepare_external
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(302, headers={'Location': 'http://other/steal'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(ProbeError, match='302'):
            await prepare_external(client, 'http://fc', 'sling:abc', 'service-token')
    assert paths == ['/api/external-player/v1/prepare']
