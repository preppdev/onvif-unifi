"""Generate mediamtx.yml and supervise the MediaMTX process.

One MediaMTX instance bound to 0.0.0.0:<rtsp_port> serves every camera's RTSP.
Because the virtual IPs live on this host, a client hitting rtsp://<vip>:8554/chN
is answered by the same instance — so the stream appears to originate from the
camera's unique IP, reinforcing the per-camera identity UniFi adopted.

Default mode is copy (sourceOnDemand proxy): zero re-encode, automatic reconnect.
Transcode is opt-in per profile (only needed when a channel is H.265 and you want
H.264 for browser preview, etc.)."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from typing import Iterable

import yaml

from .config import MediaMtxConfig
from .models import StreamProfile, VirtualCamera

log = logging.getLogger("mediamtx")


def _transcode_cmd(src: str, dest: str, p: StreamProfile) -> str:
    # libx264 copy-of-last-resort. Audio copied through untouched.
    return (
        "ffmpeg -nostdin -rtsp_transport tcp -i {src} "
        "-c:v libx264 -preset ultrafast -tune zerolatency "
        "-b:v {br}k -maxrate {br}k -bufsize {br2}k -g {fps} -r {fps} "
        "-c:a copy -f rtsp -rtsp_transport tcp {dest}"
    ).format(src=src, dest=dest, br=p.bitrate, br2=p.bitrate * 2, fps=p.framerate)


def build_paths(cameras: Iterable[VirtualCamera], rtsp_port: int) -> dict:
    paths: dict[str, dict] = {}
    for cam in cameras:
        for p in cam.profiles():
            if p.transcode:
                dest = f"rtsp://127.0.0.1:{rtsp_port}/{p.path}"
                paths[p.path] = {
                    "runOnDemand": _transcode_cmd(p.source_url, dest, p),
                    "runOnDemandRestart": True,
                    "runOnDemandStartTimeout": "10s",
                    "runOnDemandCloseAfter": "10s",
                }
            else:
                paths[p.path] = {
                    "source": p.source_url,
                    "rtspTransport": "tcp",
                    "sourceOnDemand": True,
                    "sourceOnDemandStartTimeout": "10s",
                    "sourceOnDemandCloseAfter": "10s",
                }
    return paths


def write_config(cfg: MediaMtxConfig, cameras: Iterable[VirtualCamera]) -> str:
    doc = {
        "logLevel": "info",
        "rtspAddress": f":{cfg.rtsp_port}",
        "rtspTransports": ["tcp"],
        "api": True,
        "apiAddress": f"127.0.0.1:{cfg.api_port}",
        "hls": False,
        "webrtc": False,
        "rtmp": False,
        "paths": build_paths(cameras, cfg.rtsp_port),
    }
    os.makedirs(os.path.dirname(cfg.config_path) or ".", exist_ok=True)
    with open(cfg.config_path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    log.info("wrote mediamtx config: %s (%d paths)", cfg.config_path, len(doc["paths"]))
    return cfg.config_path


class MediaMtxProcess:
    def __init__(self, cfg: MediaMtxConfig):
        self._cfg = cfg
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        binary = shutil.which(self._cfg.binary) or self._cfg.binary
        if not (shutil.which(self._cfg.binary) or os.path.exists(binary)):
            raise FileNotFoundError(
                f"mediamtx binary not found: {self._cfg.binary} "
                "(install from github.com/bluenviron/mediamtx/releases)"
            )
        log.info("starting mediamtx: %s %s", binary, self._cfg.config_path)
        self._proc = subprocess.Popen([binary, self._cfg.config_path])

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if not self._proc:
            return
        self._proc.send_signal(signal.SIGTERM)
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        log.info("mediamtx stopped")
        self._proc = None
