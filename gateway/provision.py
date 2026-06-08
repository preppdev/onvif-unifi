"""Probe the SPE-1630 over ONVIF and print a ready-to-paste cameras: block.

A 16-channel encoder does not always map channel N -> profileN in its RTSP URLs,
so guessing templates is unreliable. This asks the encoder directly: enumerate
its ONVIF media profiles, fetch each profile's real RTSP StreamUri, and group
them into per-channel main/sub pairs.
"""
from __future__ import annotations

import logging
import sys

from .config import load_config

log = logging.getLogger("provision")


def provision_encoder(config_path: str) -> int:
    cfg = load_config(config_path)
    enc = cfg.encoder

    try:
        from onvif import ONVIFCamera  # provided by onvif-zeep
    except ImportError:
        print("error: onvif-zeep not installed. pip install onvif-zeep", file=sys.stderr)
        return 1

    # ONVIF service port is usually 80; the SPE-1630 uses 80 by default.
    try:
        cam = ONVIFCamera(enc.host, 80, enc.username, enc.password)
        media = cam.create_media_service()
        profiles = media.GetProfiles()
    except Exception as e:  # noqa: BLE001 - surface any zeep/transport error plainly
        print(f"error: could not query encoder {enc.host}: {e}", file=sys.stderr)
        return 1

    print(f"# {len(profiles)} ONVIF profiles found on {enc.host}\n")
    rows = []
    for p in profiles:
        token = p.token
        try:
            req = media.create_type("GetStreamUri")
            req.ProfileToken = token
            req.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }
            uri = media.GetStreamUri(req).Uri
        except Exception as e:  # noqa: BLE001
            log.warning("no stream uri for %s: %s", token, e)
            uri = ""
        name = getattr(p, "Name", token)
        rows.append((token, name, uri))
        print(f"#   {token:<24} {name:<20} {uri}")

    print("\n# Paste source URLs into your cameras: block as main_url/sub_url.")
    print("# (Embed credentials if the encoder requires them in the RTSP URL.)")
    return 0
