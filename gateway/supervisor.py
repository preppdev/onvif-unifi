"""Orchestrate the full gateway: VNICs + MediaMTX + per-camera ONVIF, with a watchdog."""
from __future__ import annotations

import logging
import signal
import threading

from . import vnic
from .config import Config
from .mediamtx import MediaMtxProcess, write_config
from .onvif.server import CameraRuntime

log = logging.getLogger("supervisor")


class Supervisor:
    def __init__(self, cfg: Config, *, watchdog_interval: int = 15):
        self.cfg = cfg
        self._runtimes: list[CameraRuntime] = []
        self._mediamtx = MediaMtxProcess(cfg.mediamtx)
        self._keepalive = vnic.ArpKeepalive(cfg.cameras)
        self._stop = threading.Event()
        self._watchdog_interval = watchdog_interval

    def start(self) -> None:
        log.info("starting gateway: %d cameras on %s",
                 len(self.cfg.cameras), self.cfg.network.parent_interface)

        # 1. Network — bring up every virtual NIC first so binds succeed.
        vnic.setup_all(self.cfg.cameras)
        self._keepalive.start()

        # 2. RTSP relay — one MediaMTX serving all channels.
        write_config(self.cfg.mediamtx, self.cfg.cameras)
        self._mediamtx.start()

        # 3. ONVIF — one SOAP server + discovery responder per camera.
        for cam in self.cfg.cameras:
            rt = CameraRuntime(cam)
            rt.start()
            self._runtimes.append(rt)

        log.info("gateway up. %d ONVIF cameras advertising.", len(self._runtimes))

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self._signal)
        signal.signal(signal.SIGTERM, self._signal)
        while not self._stop.wait(self._watchdog_interval):
            self._watchdog()
        self.stop()

    def _watchdog(self) -> None:
        if not self._mediamtx.is_alive():
            log.warning("mediamtx died; restarting")
            try:
                self._mediamtx.start()
            except Exception as e:  # noqa: BLE001
                log.error("mediamtx restart failed: %s", e)
        for rt in self._runtimes:
            if not rt.is_alive():
                log.warning("cam%s ONVIF server died; restarting", rt.cam.id)
                try:
                    rt.stop()
                except Exception:  # noqa: BLE001
                    pass
                rt.start()

    def _signal(self, signum, _frame) -> None:
        log.info("received signal %s; shutting down", signum)
        self._stop.set()

    def stop(self) -> None:
        for rt in self._runtimes:
            try:
                rt.stop()
            except Exception:  # noqa: BLE001
                pass
        self._mediamtx.stop()
        self._keepalive.stop()
        vnic.teardown_all(self.cfg.cameras)
        log.info("gateway stopped; virtual NICs removed")
