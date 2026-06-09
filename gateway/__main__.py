"""onvif-gateway CLI: up | down | status | validate | provision."""
from __future__ import annotations

import argparse
import sys

from .config import load_config
from .logging_setup import setup_logging


def _cmd_validate(args) -> int:
    cfg = load_config(args.config)
    print(f"OK: {len(cfg.cameras)} cameras, encoder {cfg.encoder.host}, "
          f"parent {cfg.network.parent_interface}")
    for cam in cfg.cameras:
        print(f"  ch{cam.id:>2} {cam.name:<16} ip={cam.ip:<15} mac={cam.mac} "
              f"-> {cam.main.source_url}")
    return 0


def _cmd_status(args) -> int:
    import requests
    import socket
    from urllib.parse import urlparse

    cfg = load_config(args.config)
    for cam in cfg.cameras:
        url = f"http://{cam.ip}:{cam.onvif_port}/healthz"
        try:
            r = requests.get(url, timeout=2)
            state = "UP  " if r.ok else f"HTTP{r.status_code}"
        except requests.RequestException:
            state = "DOWN"
        src = "src?"
        if args.check_sources:
            p = urlparse(cam.main.source_url)
            host, port = p.hostname, p.port or 554
            try:
                with socket.create_connection((host, port), timeout=2):
                    src = "src:OK"
            except OSError:
                src = "src:X "
        print(f"  ch{cam.id:>2} onvif:{state} {src} {cam.ip}:{cam.onvif_port}  {cam.name}")
    return 0


def _cmd_up(args) -> int:
    from .supervisor import Supervisor

    cfg = load_config(args.config)
    sup = Supervisor(cfg)
    sup.start()
    sup.run_forever()
    return 0


def _cmd_down(args) -> int:
    from . import vnic

    cfg = load_config(args.config)
    vnic.teardown_all(cfg.cameras)
    print(f"removed {len(cfg.cameras)} virtual NICs")
    return 0


def _cmd_provision(args) -> int:
    from .provision import provision_encoder

    return provision_encoder(args.config)


def _cmd_agent(args) -> int:
    from .agent import FleetAgent

    cfg = load_config(args.config)
    if not cfg.fleet or not cfg.fleet.enabled:
        print("error: no enabled [fleet] section in config", file=sys.stderr)
        return 1
    FleetAgent(cfg).run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onvif-gateway")
    parser.add_argument("-c", "--config", default="gateway.yaml", help="path to config YAML")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("up", help="bring up all virtual ONVIF cameras (foreground)")
    sub.add_parser("down", help="tear down all virtual NICs")
    p_status = sub.add_parser("status", help="ping each camera's health endpoint")
    p_status.add_argument("--check-sources", action="store_true",
                          help="also TCP-probe each upstream encoder RTSP port")
    sub.add_parser("validate", help="load + check config, print resolved cameras")
    sub.add_parser("provision", help="ONVIF-probe the encoder and print a cameras: block")
    sub.add_parser("agent", help="run the fleet heartbeat agent (foreground)")

    args = parser.parse_args(argv)
    setup_logging(args.log_level)

    dispatch = {
        "up": _cmd_up,
        "down": _cmd_down,
        "status": _cmd_status,
        "validate": _cmd_validate,
        "provision": _cmd_provision,
        "agent": _cmd_agent,
    }
    try:
        return dispatch[args.command](args)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
