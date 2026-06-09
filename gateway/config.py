"""Load + validate gateway.yaml into a typed Config of VirtualCameras."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .models import StreamProfile, VirtualCamera


@dataclass
class EncoderConfig:
    host: str
    username: str
    password: str
    # Templates resolve {host} {user} {pass} {ch}. The provisioner (Phase 4) can
    # overwrite per-camera source URLs with values read from the encoder via ONVIF.
    rtsp_main_template: str = "rtsp://{user}:{pass}@{host}/profile{ch}/media.smp"
    rtsp_sub_template: Optional[str] = "rtsp://{user}:{pass}@{host}/profile{ch}_sub/media.smp"

    def url(self, template: str, ch: int) -> str:
        return template.format(host=self.host, user=self.username, **{"pass": self.password}, ch=ch)


@dataclass
class NetworkConfig:
    parent_interface: str = "eth0"
    netmask: str = "255.255.255.0"
    gateway: Optional[str] = None


@dataclass
class MediaMtxConfig:
    binary: str = "mediamtx"
    config_path: str = "/etc/onvif-gateway/mediamtx.yml"
    rtsp_port: int = 8554
    api_port: int = 9997


@dataclass
class FleetConfig:
    """Optional phone-home heartbeat + command-queue agent settings."""

    enabled: bool = False
    server_url: str = ""          # e.g. https://fleet.example.com
    box_id: str = ""              # unique id for this appliance
    api_key: str = ""             # per-box bearer token
    enroll_key: str = ""          # sent only to register an unknown box_id
    interval: int = 60            # heartbeat seconds


@dataclass
class Config:
    encoder: EncoderConfig
    network: NetworkConfig
    mediamtx: MediaMtxConfig
    cameras: list[VirtualCamera] = field(default_factory=list)
    fleet: Optional[FleetConfig] = None


def _profile(
    *,
    token: str,
    name: str,
    source_url: str,
    path: str,
    spec: dict[str, Any],
    codec: str,
) -> StreamProfile:
    return StreamProfile(
        token=token,
        name=name,
        source_url=source_url,
        path=path,
        codec=spec.get("codec", codec),
        width=int(spec.get("width", 1920)),
        height=int(spec.get("height", 1080)),
        framerate=int(spec.get("framerate", 30)),
        bitrate=int(spec.get("bitrate", 4096)),
        transcode=bool(spec.get("transcode", False)),
    )


def load_config(path: str) -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}

    enc_raw = raw.get("encoder") or {}
    encoder = EncoderConfig(
        host=_require(enc_raw, "host", "encoder.host"),
        username=enc_raw.get("username", "admin"),
        password=enc_raw.get("password", ""),
        rtsp_main_template=enc_raw.get(
            "rtsp_main_template", EncoderConfig.rtsp_main_template
        ),
        rtsp_sub_template=enc_raw.get("rtsp_sub_template", EncoderConfig.rtsp_sub_template),
    )

    net_raw = raw.get("network") or {}
    network = NetworkConfig(
        parent_interface=net_raw.get("parent_interface", "eth0"),
        netmask=net_raw.get("netmask", "255.255.255.0"),
        gateway=net_raw.get("gateway"),
    )

    mtx_raw = raw.get("mediamtx") or {}
    mediamtx = MediaMtxConfig(
        binary=mtx_raw.get("binary", "mediamtx"),
        config_path=mtx_raw.get("config_path", "/etc/onvif-gateway/mediamtx.yml"),
        rtsp_port=int(mtx_raw.get("rtsp_port", 8554)),
        api_port=int(mtx_raw.get("api_port", 9997)),
    )

    defaults = raw.get("defaults") or {}
    main_spec = defaults.get("main") or {}
    sub_spec = defaults.get("sub") or {}
    codec = defaults.get("codec", "H264")

    cameras: list[VirtualCamera] = []
    seen_ips: set[str] = set()
    seen_ids: set[int] = set()
    for entry in raw.get("cameras") or []:
        cid = int(_require(entry, "id", "cameras[].id"))
        ip = _require(entry, "ip", f"cameras[id={cid}].ip")
        if cid in seen_ids:
            raise ValueError(f"duplicate camera id: {cid}")
        if ip in seen_ips:
            raise ValueError(f"duplicate camera ip: {ip}")
        seen_ids.add(cid)
        seen_ips.add(ip)

        name = entry.get("name", f"Channel {cid}")
        # Per-camera spec can override defaults.
        cam_main_spec = {**main_spec, **(entry.get("main") or {})}
        cam_sub_spec = {**sub_spec, **(entry.get("sub") or {})}

        main_src = entry.get("main_url") or encoder.url(encoder.rtsp_main_template, cid)
        main = _profile(
            token=f"profile_main_{cid}",
            name="MainStream",
            source_url=main_src,
            path=f"ch{cid}_main",
            spec=cam_main_spec,
            codec=codec,
        )

        sub = None
        if not entry.get("disable_substream") and encoder.rtsp_sub_template:
            sub_src = entry.get("sub_url") or encoder.url(encoder.rtsp_sub_template, cid)
            sub = _profile(
                token=f"profile_sub_{cid}",
                name="SubStream",
                source_url=sub_src,
                path=f"ch{cid}_sub",
                spec=cam_sub_spec,
                codec=codec,
            )

        cameras.append(
            VirtualCamera(
                id=cid,
                name=name,
                ip=ip,
                netmask=entry.get("netmask", network.netmask),
                gateway=entry.get("gateway", network.gateway),
                parent_interface=entry.get("parent_interface", network.parent_interface),
                onvif_port=int(entry.get("onvif_port", defaults.get("onvif_port", 80))),
                rtsp_port=mediamtx.rtsp_port,
                main=main,
                sub=sub,
                username=entry.get("onvif_username", defaults.get("onvif_username", "admin")),
                password=entry.get("onvif_password", defaults.get("onvif_password", "admin")),
                mac=entry.get("mac", ""),
            )
        )

    if not cameras:
        raise ValueError("no cameras defined in config")

    fleet = None
    fleet_raw = raw.get("fleet")
    if fleet_raw:
        fleet = FleetConfig(
            enabled=bool(fleet_raw.get("enabled", False)),
            server_url=str(fleet_raw.get("server_url", "")).rstrip("/"),
            box_id=fleet_raw.get("box_id", ""),
            api_key=fleet_raw.get("api_key", ""),
            enroll_key=fleet_raw.get("enroll_key", ""),
            interval=int(fleet_raw.get("interval", 60)),
        )

    return Config(encoder=encoder, network=network, mediamtx=mediamtx,
                  cameras=cameras, fleet=fleet)


def _require(d: dict, key: str, label: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ValueError(f"missing required config value: {label}")
    return d[key]
