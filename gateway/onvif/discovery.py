"""WS-Discovery responder, one per virtual camera.

Each camera joins the WS-Discovery multicast group (239.255.255.250:3702) on its
own virtual IP and answers Probe requests with a ProbeMatch. The reply MUST be
sent from a socket bound to the camera's virtual IP, otherwise UniFi sees the
ProbeMatch arrive from the host IP and either ignores it or collapses all 16
cameras into one device. That source-IP binding is the whole trick.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
import threading
import uuid as _uuid

from ..models import VirtualCamera

log = logging.getLogger("onvif.discovery")

WSD_GROUP = "239.255.255.250"
WSD_PORT = 3702


def _probe_match(cam: VirtualCamera, relates_to: str) -> str:
    msg_id = f"urn:uuid:{_uuid.uuid4()}"
    xaddr = cam.device_service_url
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<s:Header>"
        "<a:MessageID>" + msg_id + "</a:MessageID>"
        "<a:RelatesTo>" + relates_to + "</a:RelatesTo>"
        "<a:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:To>"
        "<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>"
        "</s:Header><s:Body>"
        "<d:ProbeMatches><d:ProbeMatch>"
        "<a:EndpointReference><a:Address>urn:uuid:" + cam.uuid + "</a:Address></a:EndpointReference>"
        "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "<d:Scopes>"
        "onvif://www.onvif.org/type/video_encoder "
        "onvif://www.onvif.org/Profile/Streaming "
        "onvif://www.onvif.org/name/" + cam.name.replace(" ", "_") + " "
        "onvif://www.onvif.org/hardware/SPE-1630-CH" + str(cam.id) +
        "</d:Scopes>"
        "<d:XAddrs>" + xaddr + "</d:XAddrs>"
        "<d:MetadataVersion>1</d:MetadataVersion>"
        "</d:ProbeMatch></d:ProbeMatches>"
        "</s:Body></s:Envelope>"
    )


class DiscoveryResponder:
    def __init__(self, cam: VirtualCamera):
        self._cam = cam
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"wsd-{self._cam.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        cam = self._cam
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.bind(("", WSD_PORT))
            # Join the multicast group on this camera's virtual IP only.
            mreq = struct.pack("4s4s", socket.inet_aton(WSD_GROUP), socket.inet_aton(cam.effective_ip))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(1.0)
            self._sock = sock
        except OSError as e:
            log.error("cam%s: cannot start WS-Discovery on %s: %s", cam.id, cam.effective_ip, e)
            return

        log.info("cam%s: WS-Discovery listening on %s", cam.id, cam.effective_ip)
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if b"Probe" not in data:
                continue
            relates = self._extract_message_id(data)
            reply = _probe_match(cam, relates)
            try:
                # Source the reply from the virtual IP, ephemeral port.
                out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                out.bind((cam.effective_ip, 0))
                out.sendto(reply.encode("utf-8"), addr)
                out.close()
                log.debug("cam%s: ProbeMatch -> %s", cam.id, addr)
            except OSError as e:
                log.debug("cam%s: ProbeMatch send failed: %s", cam.id, e)

    @staticmethod
    def _extract_message_id(data: bytes) -> str:
        m = re.search(rb"<[\w:]*MessageID>\s*([^<]+?)\s*</[\w:]*MessageID>", data)
        return m.group(1).decode().strip() if m else "urn:uuid:00000000-0000-0000-0000-000000000000"
