"""SSDP (UDP 1900) and SiliconDust UDP (65001) discovery responders."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SSDP_PORT = 1900
SSDP_ADDR = "239.255.255.250"
SD_DISCOVER_PORT = 65001

# SiliconDust discovery packet types / tags (from hdhriptv).
_TYPE_DISCOVER_REQ = 0x0002
_TYPE_DISCOVER_RPY = 0x0003
_TAG_DEVICE_TYPE = 0x01
_TAG_DEVICE_ID = 0x02
_TAG_TUNER_COUNT = 0x10
_TAG_LINEUP_URL = 0x27
_TAG_BASE_URL = 0x2A
_TAG_DEVICE_AUTH = 0x2B
_TAG_MULTI_TYPE = 0x2D
_DEVICE_TYPE_TUNER = 0x00000001
_DEVICE_TYPE_WILDCARD = 0xFFFFFFFF
_DEVICE_ID_WILDCARD = 0xFFFFFFFF


def _safe_device_id_int(hex_id: str) -> int:
    cleaned = hex_id.strip().upper().replace("-", "")
    try:
        return int(cleaned, 16) & 0xFFFFFFFF
    except ValueError:
        return zlib.crc32(cleaned.encode("utf-8")) & 0xFFFFFFFF


def _read_varlen(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("missing varlen")
    first = data[offset]
    if first & 0x80 == 0:
        return first, offset + 1
    if offset + 1 >= len(data):
        raise ValueError("truncated two-byte varlen")
    length = (first & 0x7F) | (data[offset + 1] << 7)
    return length, offset + 2


def _append_varlen(buf: bytearray, length: int) -> None:
    if length < 0 or length > 0x7FFF:
        raise ValueError("invalid varlen")
    if length <= 0x7F:
        buf.append(length)
    else:
        buf.append((length & 0x7F) | 0x80)
        buf.append(length >> 7)


def _u32be(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFFFF)


@dataclass
class DiscoverIdentity:
    device_id_hex: str
    tuner_count: int
    base_url: str
    friendly_name: str = "APITuner"

    @property
    def device_id(self) -> int:
        return _safe_device_id_int(self.device_id_hex)

    @property
    def lineup_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/lineup.json"

    @property
    def device_auth(self) -> str:
        return self.device_id_hex.upper()

    @property
    def device_xml_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/device.xml"


def parse_discover_request(frame: bytes) -> Optional[tuple[list[int], int]]:
    """Parse a SiliconDust discover request. Returns (device_types, device_id)."""
    if len(frame) < 8:
        return None
    pkt_type = struct.unpack(">H", frame[0:2])[0]
    payload_len = struct.unpack(">H", frame[2:4])[0]
    total = 4 + payload_len + 4
    if total > len(frame):
        return None
    frame = frame[:total]
    expected_crc = struct.unpack("<I", frame[4 + payload_len :])[0]
    actual_crc = zlib.crc32(frame[: 4 + payload_len]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        return None
    if pkt_type != _TYPE_DISCOVER_REQ:
        return None

    payload = frame[4 : 4 + payload_len]
    device_types: list[int] = []
    device_id = _DEVICE_ID_WILDCARD
    pos = 0
    while pos < len(payload):
        tag = payload[pos]
        pos += 1
        try:
            length, pos = _read_varlen(payload, pos)
        except ValueError:
            return None
        if pos + length > len(payload):
            return None
        value = payload[pos : pos + length]
        pos += length
        if tag == _TAG_DEVICE_TYPE and length == 4:
            device_types.append(struct.unpack(">I", value)[0])
        elif tag == _TAG_MULTI_TYPE and length % 4 == 0:
            for i in range(0, length, 4):
                device_types.append(struct.unpack(">I", value[i : i + 4])[0])
        elif tag == _TAG_DEVICE_ID and length == 4:
            device_id = struct.unpack(">I", value)[0]
    return device_types, device_id


def wants_tuner(device_types: list[int]) -> bool:
    if not device_types:
        return True
    return any(t in (_DEVICE_TYPE_WILDCARD, _DEVICE_TYPE_TUNER) for t in device_types)


def build_discover_response(identity: DiscoverIdentity) -> bytes:
    """Build a SiliconDust discover reply frame."""
    tags: list[tuple[int, bytes]] = [
        (_TAG_DEVICE_TYPE, _u32be(_DEVICE_TYPE_TUNER)),
        (_TAG_DEVICE_ID, _u32be(identity.device_id)),
        (_TAG_TUNER_COUNT, bytes([max(0, min(255, identity.tuner_count))])),
    ]
    base = identity.base_url.strip()
    if base:
        tags.append((_TAG_BASE_URL, base.encode("utf-8")))
    lineup = identity.lineup_url.strip()
    if lineup:
        tags.append((_TAG_LINEUP_URL, lineup.encode("utf-8")))
    auth = identity.device_auth.strip()
    if auth:
        tags.append((_TAG_DEVICE_AUTH, auth.encode("utf-8")))

    payload = bytearray()
    for tag, value in tags:
        payload.append(tag)
        _append_varlen(payload, len(value))
        payload.extend(value)

    frame = bytearray(4 + len(payload) + 4)
    struct.pack_into(">H", frame, 0, _TYPE_DISCOVER_RPY)
    struct.pack_into(">H", frame, 2, len(payload))
    frame[4 : 4 + len(payload)] = payload
    crc = zlib.crc32(frame[: 4 + len(payload)]) & 0xFFFFFFFF
    struct.pack_into("<I", frame, 4 + len(payload), crc)
    return bytes(frame)


def _local_ipv4() -> str:
    """Best-effort primary LAN IPv4 for SSDP LOCATION URLs."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _http_port_from_base(base_url: str, fallback: int) -> int:
    parts = urlsplit(base_url)
    if parts.port:
        return parts.port
    return fallback


class HdhrDiscoveryService:
    """Background SSDP + SiliconDust UDP discovery responders."""

    def __init__(
        self,
        identity_fn: Callable[[], DiscoverIdentity],
        *,
        ssdp_enabled: bool = True,
        udp_enabled: bool = True,
        http_port: int = 6592,
    ) -> None:
        self._identity_fn = identity_fn
        self._ssdp_enabled = ssdp_enabled
        self._udp_enabled = udp_enabled
        self._http_port = http_port
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        if self._ssdp_enabled:
            self._tasks.append(asyncio.create_task(self._ssdp_loop(), name="hdhr-ssdp"))
        if self._udp_enabled:
            self._tasks.append(
                asyncio.create_task(self._udp_loop(), name="hdhr-udp65001")
            )
        if self._tasks:
            logger.info(
                "HDHR discovery started (ssdp=%s udp65001=%s)",
                self._ssdp_enabled,
                self._udp_enabled,
            )

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    def _build_ssdp_response(self, identity: DiscoverIdentity, st: str) -> bytes:
        usn_uuid = f"uuid:{identity.device_id_hex.upper()}"
        location = identity.device_xml_url
        # Prefer advertised BaseURL host; fall back to detected LAN IP.
        try:
            host = urlsplit(identity.base_url).hostname or _local_ipv4()
            port = _http_port_from_base(identity.base_url, self._http_port)
            location = f"http://{host}:{port}/device.xml"
        except Exception:  # noqa: BLE001
            pass
        lines = [
            "HTTP/1.1 200 OK",
            "CACHE-CONTROL: max-age=1800",
            f"LOCATION: {location}",
            "SERVER: APITuner/HDHomeRun UPnP/1.0",
            f"ST: {st}",
            f"USN: {usn_uuid}::{st}" if st != usn_uuid else f"USN: {usn_uuid}",
            "EXT:",
            "",
            "",
        ]
        return "\r\n".join(lines).encode("utf-8")

    async def _ssdp_loop(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.bind(("", SSDP_PORT))
            mreq = struct.pack("=4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton("0.0.0.0"))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)

            while not self._stop.is_set():
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 2048), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                except OSError as exc:
                    logger.debug("SSDP recv error: %s", exc)
                    await asyncio.sleep(1.0)
                    continue

                try:
                    text = data.decode("utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                if "M-SEARCH" not in text.upper():
                    continue
                st = "upnp:rootdevice"
                for line in text.splitlines():
                    if line.upper().startswith("ST:"):
                        st = line.split(":", 1)[1].strip()
                        break
                st_lower = st.lower()
                if st_lower not in (
                    "ssdp:all",
                    "upnp:rootdevice",
                    "urn:schemas-upnp-org:device:mediaserver:1",
                    "urn:schemas-upnp-org:device:basic:1",
                ) and "hdhomerun" not in st_lower:
                    # Still answer common rootdevice scans; ignore unrelated ST.
                    if st_lower != "ssdp:all" and "upnp:rootdevice" not in st_lower:
                        continue

                identity = self._identity_fn()
                reply_st = st if st_lower != "ssdp:all" else "upnp:rootdevice"
                reply = self._build_ssdp_response(identity, reply_st)
                try:
                    await loop.sock_sendto(sock, reply, addr)
                except OSError as exc:
                    logger.debug("SSDP send error: %s", exc)
        except OSError as exc:
            logger.warning("SSDP discovery disabled (bind failed): %s", exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    async def _udp_loop(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", SD_DISCOVER_PORT))
            sock.setblocking(False)

            while not self._stop.is_set():
                try:
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 2048), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                except OSError as exc:
                    logger.debug("UDP65001 recv error: %s", exc)
                    await asyncio.sleep(1.0)
                    continue

                parsed = parse_discover_request(data)
                if parsed is None:
                    continue
                device_types, want_id = parsed
                identity = self._identity_fn()
                if not wants_tuner(device_types):
                    continue
                if want_id not in (_DEVICE_ID_WILDCARD, identity.device_id):
                    continue
                # Prefer LAN-reachable BaseURL for discovery replies.
                host = _local_ipv4()
                identity = DiscoverIdentity(
                    device_id_hex=identity.device_id_hex,
                    tuner_count=identity.tuner_count,
                    base_url=f"http://{host}:{self._http_port}",
                    friendly_name=identity.friendly_name,
                )
                reply = build_discover_response(identity)
                try:
                    await loop.sock_sendto(sock, reply, addr)
                except OSError as exc:
                    logger.debug("UDP65001 send error: %s", exc)
        except OSError as exc:
            logger.warning("SiliconDust UDP discovery disabled (bind failed): %s", exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass
