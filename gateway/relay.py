"""On-demand TCP relays for click-to-open device UIs.

Forwards a port on the box's Tailscale IP straight through to a LAN host:port.
The browser (on the tailnet) hits http(s)://<box-tailscale-ip>:<port> and lands
on the device's web UI — no subnet route, clash-proof (you address the box, not
the LAN subnet), and bound to the tailnet IP so only tailnet members can reach it.
Transparent TCP (no HTTP rewriting), so device UIs render as-is.
"""
from __future__ import annotations

import logging
import socket
import threading
import time

log = logging.getLogger("relay")

BASE_PORT = 21000
RELAY_TTL = 3600  # auto-disable relays after this many seconds (safety net)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class _Forwarder:
    def __init__(self, bind_ip: str, listen_port: int, target_ip: str, target_port: int):
        self.bind_ip, self.listen_port = bind_ip, listen_port
        self.target_ip, self.target_port = target_ip, target_port
        self._srv: socket.socket | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_ip, self.listen_port))
        srv.listen(16)
        srv.settimeout(1.0)
        self._srv = srv
        threading.Thread(target=self._accept, name=f"relay-{self.listen_port}", daemon=True).start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                upstream = socket.create_connection((self.target_ip, self.target_port), timeout=5)
            except OSError as e:
                log.debug("relay %s->%s:%s connect failed: %s",
                          self.listen_port, self.target_ip, self.target_port, e)
                client.close()
                continue
            threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()
            threading.Thread(target=_pipe, args=(upstream, client), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass


class RelayManager:
    """Module-level singleton owning the active forwarders. Toggled by the
    relays_on / relays_off fleet commands; idempotent."""

    def __init__(self) -> None:
        self._fwd: dict[str, _Forwarder] = {}
        self._urls: dict[str, str] = {}
        self._enabled = False
        self._enabled_at = 0.0

    def enable(self, bind_ip: str, targets: list[dict]) -> int:
        """targets: [{ip, port, scheme}]. Returns the number of relays started."""
        self.disable()
        if not bind_ip:
            return 0
        port = BASE_PORT
        for t in targets:
            try:
                f = _Forwarder(bind_ip, port, t["ip"], int(t["port"]))
                f.start()
            except OSError as e:
                log.warning("relay for %s failed: %s", t.get("ip"), e)
                continue
            self._fwd[t["ip"]] = f
            self._urls[t["ip"]] = f'{t.get("scheme", "http")}://{bind_ip}:{port}'
            port += 1
        self._enabled = True
        self._enabled_at = time.time()
        log.info("relays enabled: %d host(s) on %s (auto-off in %ds)",
                 len(self._fwd), bind_ip, RELAY_TTL)
        return len(self._fwd)

    def disable(self) -> None:
        for f in self._fwd.values():
            f.stop()
        self._fwd.clear()
        self._urls.clear()
        self._enabled = False
        self._enabled_at = 0.0

    def seconds_remaining(self) -> int:
        if not self._enabled:
            return 0
        return max(0, int(RELAY_TTL - (time.time() - self._enabled_at)))

    def expire_if_due(self) -> bool:
        """Auto-disable relays once their TTL is up. Returns True if it expired."""
        if self._enabled and time.time() - self._enabled_at >= RELAY_TTL:
            self.disable()
            return True
        return False

    def urls(self) -> dict[str, str]:
        return dict(self._urls)

    @property
    def enabled(self) -> bool:
        return self._enabled


RELAYS = RelayManager()
