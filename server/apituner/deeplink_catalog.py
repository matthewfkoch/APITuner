"""Map FruitDeepLinks providers / URL schemes to Android TV packages."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional
from urllib.parse import urlparse

DeeplinkProfile = Literal["google_tv", "fire"]

DEFAULT_PROFILE: DeeplinkProfile = "google_tv"

# Path: /api/adb/lanes/{provider}/{lane}/deeplink
_LANE_PATH = re.compile(
    r"/api/adb/lanes/([^/]+)/(\d+)(?:/deeplink)?", re.IGNORECASE
)

# (google_tv_primary, fire_primary). Alternate is always the other family.
# ESPN split matches dashboard copy: Google → score_center, Fire → gtv.
_PACKAGES: dict[str, tuple[str, str]] = {
    "sportscenter": (
        "com.espn.score_center",
        "com.espn.gtv",
    ),
    "sportsonespn": (
        "com.espn.score_center",
        "com.espn.gtv",
    ),
    "aiv": (
        "com.amazon.amazonvideo.livingroom",
        "com.amazon.avod",
    ),
    "gametime": (
        "com.amazon.amazonvideo.livingroom",
        "com.amazon.avod",
    ),
    "pplus": (
        "com.cbs.ott",
        "com.cbs.ott",
    ),
    "cbssportsapp": (
        "com.cbs.ott",
        "com.cbs.ott",
    ),
    "cbstve": (
        "com.cbs.ott",
        "com.cbs.ott",
    ),
    "max": (
        "com.wbd.stream",
        "com.wbd.stream",
    ),
    "watchtnt": (
        "com.wbd.stream",
        "com.wbd.stream",
    ),
    "watchtru": (
        "com.wbd.stream",
        "com.wbd.stream",
    ),
    "peacock": (
        "com.peacocktv.peacockandroid",
        "com.peacocktv.peacockandroid",
    ),
    "peacock_web": (
        "com.peacocktv.peacockandroid",
        "com.peacocktv.peacockandroid",
    ),
    "peacocktv": (
        "com.peacocktv.peacockandroid",
        "com.peacocktv.peacockandroid",
    ),
    "nflctv": (
        "com.gotv.nflgamecenter.us.lite",
        "com.gotv.nflgamecenter.us.lite",
    ),
    "dazn": (
        "com.dazn",
        "com.dazn",
    ),
    "vixapp": (
        "com.dla.android.vixplus",
        "com.dla.android.vixplus",
    ),
    "foxone": (
        "com.fox.foxone",
        "com.fox.now",
    ),
    "fsapp": (
        "com.foxsports.videogo",
        "com.fox.now",
    ),
    "nbcsportstve": (
        "com.nbcuni.nbc.androidtv",
        "com.nbcuni.nbc",
    ),
    "f1tv": (
        "com.formulaone.production",
        "com.formulaone.production",
    ),
    "apple_mls": (
        "com.apple.atve.androidtv.appletv",
        "com.apple.atve.amazon.appletv",
    ),
    "apple_mlb": (
        "com.apple.atve.androidtv.appletv",
        "com.apple.atve.amazon.appletv",
    ),
    "apple_nba": (
        "com.apple.atve.androidtv.appletv",
        "com.apple.atve.amazon.appletv",
    ),
    "apple_nhl": (
        "com.apple.atve.androidtv.appletv",
        "com.apple.atve.amazon.appletv",
    ),
    "apple_other": (
        "com.apple.atve.androidtv.appletv",
        "com.apple.atve.amazon.appletv",
    ),
    "yttv": (
        "com.google.android.youtube.tvunplugged",
        "com.google.android.youtube.tvunplugged",
    ),
    "mlb": (
        "com.bamnetworks.mobile.android.gameday.atbat",
        "com.bamnetworks.mobile.android.gameday.atbat",
    ),
}

_SCHEME_TO_CODE: dict[str, str] = {
    "sportscenter": "sportscenter",
    "sportsonespn": "sportsonespn",
    "aiv": "aiv",
    "gametime": "gametime",
    "pplus": "pplus",
    "cbssportsapp": "cbssportsapp",
    "cbstve": "cbstve",
    "watchtnt": "watchtnt",
    "watchtru": "watchtru",
    "peacock": "peacock",
    "peacocktv": "peacocktv",
    "nflctv": "nflctv",
    "dazn": "dazn",
    "open.dazn.com": "dazn",
    "vixapp": "vixapp",
    "foxone": "foxone",
    "fsapp": "fsapp",
    "nbcsportstve": "nbcsportstve",
}

_HOST_TO_CODE: dict[str, str] = {
    "play.hbomax.com": "max",
    "play.max.com": "max",
    "www.max.com": "max",
    "peacocktv.com": "peacock_web",
    "www.peacocktv.com": "peacock_web",
    "f1tv.formula1.com": "f1tv",
    "tv.apple.com": "apple_other",
    "app.primevideo.com": "aiv",
    "www.amazon.com": "aiv",
    "watch.amazon.com": "aiv",
    "www.espn.com": "sportscenter",
    "plus.espn.com": "sportsonespn",
    "www.paramountplus.com": "pplus",
    "tv.youtube.com": "yttv",
    "www.mlb.tv": "mlb",
    "mlb.tv": "mlb",
}

# FruitDeepLinks virtual-lane "channel_name" / display labels.
_DISPLAY_TO_CODE: dict[str, str] = {
    "espn": "sportscenter",
    "espn+": "sportsonespn",
    "espn plus": "sportsonespn",
    "prime video": "aiv",
    "amazon prime": "aiv",
    "amazon prime video": "aiv",
    "apple tv": "apple_other",
    "apple tv+": "apple_other",
    "apple mls": "apple_mls",
    "paramount+": "pplus",
    "paramount plus": "pplus",
    "cbs sports": "cbssportsapp",
    "cbs": "cbstve",
    "mlb": "mlb",
    "mlb.tv": "mlb",
    "peacock": "peacock",
    "max": "max",
    "hbo max": "max",
    "dazn": "dazn",
    "vix": "vixapp",
    "f1 tv": "f1tv",
    "fox sports": "foxone",
    "nfl+": "nflctv",
    "nfl plus": "nflctv",
    "youtube tv": "yttv",
}

_WHATSON_PATH = re.compile(r"/whatson/(\d+)", re.IGNORECASE)
_VIRTUAL_STREAM = re.compile(
    r"/lane/(\d+)(?:/stream(?:\.m3u8)?)?/?$", re.IGNORECASE
)


def normalize_profile(profile: Optional[str]) -> DeeplinkProfile:
    raw = (profile or DEFAULT_PROFILE).strip().lower().replace("-", "_")
    if raw in ("fire", "firetv", "fire_tv", "amazon"):
        return "fire"
    return "google_tv"


def packages_for(
    provider_code: Optional[str],
    *,
    profile: Optional[str] = None,
) -> Optional[tuple[str, Optional[str]]]:
    """Return (package_name, alternate_package_name) for a FDL provider code."""
    code = (provider_code or "").strip().lower()
    pair = _PACKAGES.get(code)
    if not pair:
        return None
    google_pkg, fire_pkg = pair
    if normalize_profile(profile) == "fire":
        primary, alt = fire_pkg, google_pkg
    else:
        primary, alt = google_pkg, fire_pkg
    if alt == primary:
        return primary, None
    return primary, alt


def parse_lane_url(url: str) -> Optional[tuple[str, int]]:
    """Return (provider_code, lane_number) from an FDL ADB lane resolver URL."""
    parsed = urlparse((url or "").strip())
    match = _LANE_PATH.search(parsed.path or "")
    if not match:
        return None
    return match.group(1).lower(), int(match.group(2))


def parse_whatson_url(url: str) -> Optional[int]:
    """Return lane id from /whatson/{n}."""
    parsed = urlparse((url or "").strip())
    match = _WHATSON_PATH.search(parsed.path or "")
    if not match:
        return None
    return int(match.group(1))


def parse_virtual_stream_url(url: str) -> Optional[int]:
    """Return lane id from FDL HLS /lane/{n}/stream.m3u8 (Channels STRMLINK)."""
    parsed = urlparse((url or "").strip())
    match = _VIRTUAL_STREAM.search(parsed.path or "")
    if not match:
        return None
    return int(match.group(1))


def code_from_display_name(name: Optional[str]) -> Optional[str]:
    """Map FruitDeepLinks channel_name / guide labels onto a catalog code."""
    raw = (name or "").strip().lower()
    if not raw:
        return None
    if raw in _DISPLAY_TO_CODE:
        return _DISPLAY_TO_CODE[raw]
    # "ESPN+ College" / "Prime Video TNF"
    for label, code in sorted(_DISPLAY_TO_CODE.items(), key=lambda kv: -len(kv[0])):
        if raw.startswith(label) or label in raw:
            return code
    return None


def infer_provider(url: str) -> Optional[str]:
    """Infer a catalog provider code from a resolver URL or deeplink URI."""
    raw = (url or "").strip()
    if not raw:
        return None
    lane = parse_lane_url(raw)
    if lane:
        return lane[0]
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme in _SCHEME_TO_CODE:
        return _SCHEME_TO_CODE[scheme]
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host_noprefix = host[4:]
    else:
        host_noprefix = host
    for needle, code in _HOST_TO_CODE.items():
        if host == needle or host_noprefix == needle or host.endswith("." + needle):
            return code
        if needle in host:
            return code
    return None


def resolve_packages(
    *,
    url: str = "",
    provider_code: Optional[str] = None,
    profile: Optional[str] = None,
    package_name: Optional[str] = None,
    alternate_package_name: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (provider, package_name, alternate). Explicit packages win."""
    code = (provider_code or "").strip().lower() or infer_provider(url)
    pkg = (package_name or "").strip() or None
    alt = (alternate_package_name or "").strip() or None
    if pkg:
        return code, pkg, alt
    mapped = packages_for(code, profile=profile) if code else None
    if not mapped:
        return code, None, alt
    return code, mapped[0], alt or mapped[1]


def catalog_payload() -> dict[str, Any]:
    """Machine-readable catalog for FruitDeepLinks / other integrators."""
    schemes_by_code: dict[str, list[str]] = {}
    for scheme, code in _SCHEME_TO_CODE.items():
        schemes_by_code.setdefault(code, []).append(scheme)
    hosts_by_code: dict[str, list[str]] = {}
    for host, code in _HOST_TO_CODE.items():
        hosts_by_code.setdefault(code, []).append(host)

    providers: list[dict[str, Any]] = []
    for code in sorted(_PACKAGES):
        g_pkg, f_pkg = _PACKAGES[code]
        g_primary, g_alt = packages_for(code, profile="google_tv") or (g_pkg, None)
        f_primary, f_alt = packages_for(code, profile="fire") or (f_pkg, None)
        providers.append(
            {
                "code": code,
                "schemes": schemes_by_code.get(code, []),
                "hosts": hosts_by_code.get(code, []),
                "packages": {
                    "google_tv": {
                        "package_name": g_primary,
                        "alternate_package_name": g_alt,
                    },
                    "fire": {
                        "package_name": f_primary,
                        "alternate_package_name": f_alt,
                    },
                },
            }
        )
    return {"profiles": ["google_tv", "fire"], "providers": providers}
