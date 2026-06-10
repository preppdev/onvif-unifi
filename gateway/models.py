"""Core data model: one VirtualCamera == one fake ONVIF device on its own IP/MAC."""
from __future__ import annotations

import hashlib
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Optional


def derive_mac(seed: str) -> str:
    """Deterministic, locally-administered unicast MAC from a stable seed.

    The ``02`` prefix marks the address locally-administered (bit 1 set) and
    unicast (bit 0 clear), so it will never collide with a real vendor OUI.
    Deterministic so a camera keeps the same MAC across restarts -> stable
    DHCP reservations / UniFi adoption identity.
    """
    h = hashlib.sha256(seed.encode()).hexdigest()
    return "02:" + ":".join(h[i : i + 2] for i in range(0, 10, 2))


def derive_uuid(seed: str) -> str:
    """Stable UUID for the ONVIF EndpointReference, derived from the same seed."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"onvif-gateway/{seed}"))


@dataclass
class StreamProfile:
    """A single advertised ONVIF media profile (main or sub stream)."""

    token: str                 # ONVIF profile token, unique per camera
    name: str                  # human label ("MainStream" / "SubStream")
    source_url: str            # upstream RTSP URL on the encoder
    path: str                  # MediaMTX path that re-serves this stream
    codec: str = "H264"        # H264 | H265
    width: int = 1920
    height: int = 1080
    framerate: int = 30
    bitrate: int = 4096        # kbps, advertised value only
    transcode: bool = False    # copy-codec by default; opt-in re-encode


@dataclass
class VirtualCamera:
    """One encoder channel, presented as an independent ONVIF camera."""

    id: int                    # 1-based channel index
    name: str                  # display name in UniFi
    ip: str = ""               # static IP (required when ip_mode=static; blank for dhcp)
    ip_mode: str = "static"    # "static" | "dhcp" (dhcp = lease per-VNIC at runtime)
    assigned_ip: str = ""      # runtime: the actual IP on the VNIC (dhcp lease or static)
    netmask: str = "255.255.255.0"
    gateway: Optional[str] = None
    parent_interface: str = "eth0"
    onvif_port: int = 80       # ONVIF SOAP HTTP port (per-IP, so 80 is fine)
    rtsp_port: int = 8554      # MediaMTX RTSP port (shared; per-IP listen)

    main: StreamProfile = field(default=None)  # type: ignore[assignment]
    sub: Optional[StreamProfile] = None

    username: str = "admin"
    password: str = "admin"

    mac: str = ""              # filled in __post_init__ if blank
    uuid: str = ""             # filled in __post_init__ if blank

    def __post_init__(self) -> None:
        seed = f"ch{self.id}-{self.name}"
        if not self.mac:
            self.mac = derive_mac(seed)
        if not self.uuid:
            self.uuid = derive_uuid(seed)

    @property
    def vnic_name(self) -> str:
        """Linux interface name for this camera's macvlan VNIC (<=15 chars)."""
        return f"onvif{self.id}"

    @property
    def serial(self) -> str:
        """ONVIF serial / hardware id — MAC with separators stripped."""
        return self.mac.replace(":", "").upper()

    @property
    def effective_ip(self) -> str:
        """The IP actually on the VNIC: the runtime-assigned address (DHCP lease,
        or static once added) if known, else the configured static IP."""
        return self.assigned_ip or self.ip

    @property
    def device_service_url(self) -> str:
        return f"http://{self.effective_ip}:{self.onvif_port}/onvif/device_service"

    def rtsp_url(self, profile: StreamProfile) -> str:
        """RTSP URL we hand to UniFi — points at the *virtual* IP, not the encoder."""
        return f"rtsp://{self.effective_ip}:{self.rtsp_port}/{profile.path}"

    def profiles(self) -> list[StreamProfile]:
        return [p for p in (self.main, self.sub) if p is not None]
