"""Single-frame JPEG capture via ffmpeg, for ONVIF GetSnapshotUri."""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import os
from typing import Optional

log = logging.getLogger("onvif.snapshot")


def capture_jpeg(rtsp_url: str, timeout: int = 10) -> Optional[bytes]:
    if shutil.which("ffmpeg") is None:
        log.warning("ffmpeg not on PATH; cannot capture snapshot")
        return None
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-i", rtsp_url,
                "-frames:v", "1", "-q:v", "2", "-f", "image2", "-y", path,
            ],
            capture_output=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not os.path.getsize(path):
            log.debug("snapshot ffmpeg failed: %s", proc.stderr.decode("utf-8", "replace")[:200])
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except subprocess.TimeoutExpired:
        log.debug("snapshot timed out for %s", rtsp_url)
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
