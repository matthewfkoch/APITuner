"""HDHomeRun protocol emulation for Channels DVR / Plex / Emby / Jellyfin."""

from .lineup import build_discover_json, build_lineup_json, build_device_xml

__all__ = ["build_discover_json", "build_lineup_json", "build_device_xml"]
