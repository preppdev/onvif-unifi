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
import platform
import shutil
import socket
import struct
import subprocess
import threading
import time
from typing import Iterable

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

    prefix = _prefix_len(cam.netmask)
    # No default gateway on the VNIC — that would hijack the host's outbound route.
    _run(["ip", "addr", "add", f"{cam.ip}/{prefix}", "dev", vnic], check=False)
    _run(["ip", "link", "set", vnic, "up"])
    log.info("vnic %s up: %s/%s mac=%s on %s", vnic, cam.ip, prefix, cam.mac, parent)


def teardown_camera(cam: VirtualCamera) -> None:
    if not IS_LINUX:
        return
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
