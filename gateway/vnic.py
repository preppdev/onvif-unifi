"""Per-camera macvlan virtual NICs: unique IP + MAC on one physical interface.

The hard-won part here is the ARP configuration. When you put many IPs on one
physical link via macvlan, the host's default ARP behaviour will happily answer
for and source-announce all of them through the wrong interface, which makes
UniFi see flapping/duplicate MACs. The sysctl knobs below (arp_ignore=1,
arp_announce=2) restrict each interface to only answer for and announce its own
address — this is what makes 16 cameras on one NIC stable.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import struct
import subprocess
import threading
import time
from typing import Iterable, Optional

from .models import VirtualCamera

log = logging.getLogger("vnic")

IS_LINUX = platform.system() == "Linux"


class NetworkError(RuntimeError):
    pass


def _run(cmd: list[str], *, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    log.debug("exec: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and check:
        raise NetworkError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")
    if proc.returncode != 0 and not quiet:
        log.debug("non-fatal failure: %s -> %s", " ".join(cmd), proc.stderr.strip())
    return proc


def _link_exists(name: str) -> bool:
    return _run(["ip", "link", "show", name], check=False, quiet=True).returncode == 0


def _sysctl(key: str, value: str) -> None:
    _run(["sysctl", "-w", f"{key}={value}"], check=False, quiet=True)


def _apply_arp_isolation(parent: str, vnic: str) -> None:
    for scope in ("all", parent, vnic):
        _sysctl(f"net.ipv4.conf.{scope}.arp_ignore", "1")
        _sysctl(f"net.ipv4.conf.{scope}.arp_announce", "2")


def _prefix_len(netmask: str) -> int:
    return sum(bin(int(o)).count("1") for o in netmask.split("."))


def preflight() -> None:
    """Fail early with a clear message if the host can't create VNICs."""
    if not IS_LINUX:
        raise NetworkError(
            "virtual NICs require Linux (macvlan). This host is "
            f"{platform.system()}. Run the gateway on the Linux target."
        )
    for tool in ("ip", "sysctl"):
        if shutil.which(tool) is None:
            raise NetworkError(f"required tool not found on PATH: {tool}")


def setup_camera(cam: VirtualCamera) -> None:
    """Create (idempotently) the macvlan VNIC for one camera and assign its IP."""
    parent = cam.parent_interface
    vnic = cam.vnic_name

    # Promiscuous parent so the bridge-mode macvlan children receive frames.
    _run(["ip", "link", "set", parent, "promisc", "on"], check=False)

    if _link_exists(vnic):
        log.info("vnic %s already exists; reconfiguring", vnic)
        _run(["ip", "link", "delete", vnic], check=False)

    _run(["ip", "link", "add", vnic, "link", parent, "type", "macvlan", "mode", "bridge"])
    _run(["ip", "link", "set", vnic, "address", cam.mac])
    _apply_arp_isolation(parent, vnic)
    _run(["ip", "link", "set", vnic, "up"])

    if cam.ip_mode == "dhcp":
        cam.assigned_ip = _dhcp_lease(vnic) or ""
        log.info("vnic %s up: dhcp=%s mac=%s on %s",
                 vnic, cam.assigned_ip or "(no lease)", cam.mac, parent)
    else:
        prefix = _prefix_len(cam.netmask)
        # No default gateway on the VNIC — that would hijack the host's outbound route.
        _run(["ip", "addr", "add", f"{cam.ip}/{prefix}", "dev", vnic], check=False)
        cam.assigned_ip = cam.ip
        log.info("vnic %s up: %s/%s mac=%s on %s", vnic, cam.ip, prefix, cam.mac, parent)


def _dhcp_lease(vnic: str, timeout: int = 15) -> Optional[str]:
    """Lease an address on the VNIC via dhclient and return it. A per-interface
    config keeps dhclient from clobbering the host's DNS/default route."""
    conf = f"/run/onvif-gateway/dhclient-{vnic}.conf"
    os.makedirs("/run/onvif-gateway", exist_ok=True)
    # request only an address — no routers/dns supersede of the host.
    with open(conf, "w") as fh:
        fh.write('request subnet-mask, broadcast-address;\n')
    _run(["dhclient", "-r", "-cf", conf, vnic], check=False, quiet=True)  # release stale
    _run(["dhclient", "-1", "-nw", "-cf", conf, vnic], check=False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        ip = current_ip(vnic)
        if ip:
            return ip
        time.sleep(1)
    return None


def current_ip(vnic: str) -> Optional[str]:
    """Read the IPv4 address currently on a VNIC (ground truth for dhcp + static)."""
    proc = _run(["ip", "-4", "-o", "addr", "show", vnic], check=False, quiet=True)
    for tok in proc.stdout.split():
        if "/" in tok and tok.count(".") == 3:
            return tok.split("/")[0]
    return None


def teardown_camera(cam: VirtualCamera) -> None:
    if not IS_LINUX:
        return
    if cam.ip_mode == "dhcp":
        conf = f"/run/onvif-gateway/dhclient-{cam.vnic_name}.conf"
        _run(["dhclient", "-r", "-cf", conf, cam.vnic_name], check=False, quiet=True)
    if _link_exists(cam.vnic_name):
        _run(["ip", "link", "delete", cam.vnic_name], check=False)
        log.info("vnic %s removed", cam.vnic_name)


def setup_all(cameras: Iterable[VirtualCamera]) -> None:
    preflight()
    for cam in cameras:
        setup_camera(cam)


def teardown_all(cameras: Iterable[VirtualCamera]) -> None:
    for cam in cameras:
        teardown_camera(cam)


class ArpKeepalive:
    """Periodically source a broadcast packet from each VNIC so the switch's MAC
    table and the gateway's ARP cache keep the virtual MACs fresh. Without this,
    an idle camera can vanish from the switch and UniFi marks it offline."""

    def __init__(self, cameras: list[VirtualCamera], interval: int = 60):
        self._cameras = cameras
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not IS_LINUX:
            return
        self._thread = threading.Thread(target=self._run, name="arp-keepalive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            for cam in self._cameras:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    # Bind to the virtual IP so the frame egresses that VNIC.
                    s.bind((cam.ip, 0))
                    s.sendto(b"\x00", ("255.255.255.255", 9))  # discard port
                    s.close()
                except OSError as e:  # pragma: no cover - best effort
                    log.debug("keepalive failed for %s: %s", cam.ip, e)


# Helper kept here (rather than in discovery) because joining a multicast group
# on a specific local IP needs the raw struct packing and is network-layer code.
def multicast_request_struct(group: str, local_ip: str) -> bytes:
    return struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(local_ip))
