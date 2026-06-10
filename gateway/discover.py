"""Discover other devices on the box's LAN — the UniFi console/gear and the
encoder(s) — so they surface in the fleet dashboard. Broadcast/multicast only
(no slow subnet sweep)."""
from __future__ import annotations

import logging
import re
import socket
import struct
import subprocess
import uuid as _uuid

log = logging.getLogger("discover")

# MAC OUI prefixes (first 3 octets, lowercase, no separators) -> vendor tag.
OUI_VENDORS = {
    # Ubiquiti
    "0418d6": "Ubiquiti", "245a4c": "Ubiquiti", "68d79a": "Ubiquiti", "788a20": "Ubiquiti",
    "802aa8": "Ubiquiti", "b4fbe4": "Ubiquiti", "dc9fdb": "Ubiquiti", "f09fc2": "Ubiquiti",
    "fcecda": "Ubiquiti", "00156d": "Ubiquiti", "44d9e7": "Ubiquiti", "e063da": "Ubiquiti",
    "74acb9": "Ubiquiti", "9883c4": "Ubiquiti", "d021f9": "Ubiquiti",
    # Hanwha Vision / Samsung Techwin
    "000918": "Hanwha", "001b41": "Hanwha", "e43022": "Hanwha",
    "00166c": "Hanwha", "f44210": "Hanwha",
}


def _vendor(mac: str) -> str | None:
    oui = mac.lower().replace(":", "").replace("-", "")[:6]
    return OUI_VENDORS.get(oui)


def onvif_probe(timeout: float = 3.0) -> list[dict]:
    """WS-Discovery Probe -> ProbeMatches. Finds the encoder + any ONVIF cameras."""
    msg_id = f"urn:uuid:{_uuid.uuid4()}"
    probe = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
        f"<s:Header><a:MessageID>{msg_id}</a:MessageID>"
        "<a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>"
        "<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action></s:Header>"
        "<s:Body><d:Probe><d:Types xmlns:dn=\"http://www.onvif.org/ver10/network/wsdl\">"
        "dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body></s:Envelope>"
    ).encode()
    found: dict[str, dict] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        s.sendto(probe, ("239.255.255.250", 3702))
        import time as _t
        end = _t.time() + timeout
        while _t.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                break
            xaddrs = re.search(rb"<[\w:]*XAddrs>([^<]+)</[\w:]*XAddrs>", data)
            url = xaddrs.group(1).decode().split()[0] if xaddrs else ""
            found[addr[0]] = {"ip": addr[0], "xaddr": url, "kind": "onvif"}
        s.close()
    except OSError as e:
        log.debug("onvif probe failed: %s", e)
    return list(found.values())


def unifi_probe(timeout: float = 3.0) -> list[dict]:
    """Ubiquiti L2 discovery (UDP/10001 broadcast). Finds UniFi consoles/APs/switches."""
    found: dict[str, dict] = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        s.sendto(b"\x01\x00\x00\x00", ("255.255.255.255", 10001))
        import time as _t
        end = _t.time() + timeout
        while _t.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                break
            info = {"ip": addr[0], "kind": "unifi"}
            info.update(_parse_ubnt_tlv(data))
            found[addr[0]] = info
        s.close()
    except OSError as e:
        log.debug("unifi probe failed: %s", e)
    return list(found.values())


def _parse_ubnt_tlv(data: bytes) -> dict:
    """Best-effort parse of UBNT discovery TLVs for hostname/model/mac."""
    out: dict = {}
    try:
        i = 4  # skip version+cmd+length header
        while i + 3 <= len(data):
            t = data[i]
            ln = int.from_bytes(data[i + 1:i + 3], "big")
            v = data[i + 3:i + 3 + ln]
            if t == 0x02 and ln >= 6:  # mac(6)[+ip(4)]
                out["mac"] = ":".join(f"{b:02x}" for b in v[:6])
            elif t == 0x0b:            # hostname
                out["hostname"] = v.decode("utf-8", "replace")
            elif t in (0x0c, 0x14):    # model
                out["model"] = v.decode("utf-8", "replace")
            elif t == 0x03:            # firmware/version
                out["version"] = v.decode("utf-8", "replace")[:64]
            i += 3 + ln
    except (IndexError, ValueError):
        pass
    return out


def neighbor_vendors() -> list[dict]:
    """Devices already in the kernel neighbor table, tagged by MAC vendor.
    Deduped by IP (the same device can appear via several macvlan VNICs)."""
    seen: dict[str, dict] = {}
    try:
        proc = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=5)
        for line in proc.stdout.splitlines():
            m = re.match(r"(\S+).*lladdr (\S+)", line)
            if not m:
                continue
            ip, mac = m.group(1), m.group(2)
            vendor = _vendor(mac)
            if vendor and ip not in seen:  # only recognized vendors, one row per IP
                seen[ip] = {"ip": ip, "mac": mac, "vendor": vendor}
    except (OSError, subprocess.SubprocessError):
        pass
    return list(seen.values())


def discover_all() -> dict:
    return {
        "onvif": onvif_probe(),
        "unifi": unifi_probe(),
        "tagged": neighbor_vendors(),
    }
