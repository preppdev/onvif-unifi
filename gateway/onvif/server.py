"""Bind one camera's ONVIF HTTP service to its virtual IP and run discovery."""
from __future__ import annotations

import logging
import threading

from werkzeug.serving import make_server

from ..models import VirtualCamera
from .device import build_app
from .discovery import DiscoveryResponder

log = logging.getLogger("onvif.server")


class CameraRuntime:
    """The ONVIF half of one virtual camera: an HTTP SOAP server bound to the
    camera's virtual IP plus a WS-Discovery responder. Network (VNIC) and RTSP
    (MediaMTX) are owned by the supervisor, not here."""

    def __init__(self, cam: VirtualCamera):
        self.cam = cam
        self._httpd = None
        self._http_thread: threading.Thread | None = None
        self._discovery = DiscoveryResponder(cam)

    def start(self) -> None:
        app = build_app(self.cam)
        # Bind to the camera's own IP so the SOAP service answers only there.
        self._httpd = make_server(self.cam.effective_ip, self.cam.onvif_port, app, threaded=True)
        self._http_thread = threading.Thread(
            target=self._httpd.serve_forever, name=f"onvif-http-{self.cam.id}", daemon=True
        )
        self._http_thread.start()
        self._discovery.start()
        log.info(
            "cam%s '%s' ONVIF up at %s",
            self.cam.id, self.cam.name, self.cam.device_service_url,
        )

    def is_alive(self) -> bool:
        return self._http_thread is not None and self._http_thread.is_alive()

    def stop(self) -> None:
        self._discovery.stop()
        if self._httpd:
            self._httpd.shutdown()
        if self._http_thread:
            self._http_thread.join(timeout=2)
        log.info("cam%s ONVIF stopped", self.cam.id)
