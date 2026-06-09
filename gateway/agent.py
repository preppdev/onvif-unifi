"""Fleet heartbeat agent.

Runs as its own service (separate from the gateway, so it keeps reporting even if
the gateway is down and can be told to restart/update it). Every `interval`
seconds it POSTs a status snapshot to the central server and executes any pending
commands the server returns.

Security model:
- Outbound only. The box is never listening; it polls. Works behind client NAT.
- Per-box bearer key over HTTPS. Unknown box_ids require an enroll key to register.
- Commands are a FIXED enum (reboot/update/restart/stop/start) — never arbitrary
  shell. A compromised server cannot run anything we didn't pre-authorize here.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from .config import Config, FleetConfig

log = logging.getLogger("agent")

# The only actions the server is allowed to trigger. Mapping value is a callable
# name resolved in _run_command — no shell strings come from the server.
ALLOWED_COMMANDS = {"reboot", "update", "restart_gateway", "stop_gateway", "start_gateway"}

GATEWAY_SERVICE = "onvif-gateway"


def _repo_dir() -> Path:
    # gateway/agent.py -> repo root is two levels up.
    return Path(__file__).resolve().parent.parent


def _version() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_repo_dir()), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _uptime_s() -> int:
    try:
        with open("/proc/uptime") as fh:
            return int(float(fh.read().split()[0]))
    except (OSError, ValueError):
        return 0


def _tailscale_ip() -> Optional[str]:
    if shutil.which("tailscale") is None:
        return None
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        ip = out.stdout.strip().splitlines()
        return ip[0] if ip else None
    except (OSError, subprocess.SubprocessError):
        return None


def _service_active(name: str) -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _camera_health(cfg: Config) -> list[dict]:
    cams = []
    for cam in cfg.cameras:
        up = False
        try:
            r = requests.get(f"http://{cam.ip}:{cam.onvif_port}/healthz", timeout=2)
            up = r.ok
        except requests.RequestException:
            up = False
        cams.append({"id": cam.id, "name": cam.name, "ip": cam.ip, "up": up})
    return cams


def collect_status(cfg: Config) -> dict:
    cams = _camera_health(cfg)
    return {
        "version": _version(),
        "hostname": platform.node(),
        "uptime_s": _uptime_s(),
        "tailscale_ip": _tailscale_ip(),
        "gateway_active": _service_active(GATEWAY_SERVICE),
        "cameras_total": len(cams),
        "cameras_up": sum(1 for c in cams if c["up"]),
        "cameras": cams,
    }


def _run_command(cmd_type: str) -> tuple[bool, str]:
    """Execute one pre-authorized command. Returns (ok, output)."""
    if cmd_type not in ALLOWED_COMMANDS:
        return False, f"refused: {cmd_type} not in allowlist"
    actions = {
        "restart_gateway": ["systemctl", "restart", GATEWAY_SERVICE],
        "stop_gateway": ["systemctl", "stop", GATEWAY_SERVICE],
        "start_gateway": ["systemctl", "start", GATEWAY_SERVICE],
        "update": [str(_repo_dir() / "scripts" / "update.sh")],
        "reboot": ["systemctl", "reboot"],
    }
    argv = actions[cmd_type]
    log.info("executing command: %s", cmd_type)
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        ok = out.returncode == 0
        return ok, (out.stdout + out.stderr)[-2000:]
    except subprocess.SubprocessError as e:
        return False, str(e)


class FleetAgent:
    def __init__(self, cfg: Config):
        if not cfg.fleet or not cfg.fleet.enabled:
            raise ValueError("fleet section missing or disabled in config")
        self.cfg = cfg
        self.fleet: FleetConfig = cfg.fleet
        self._stop = threading.Event()
        self._session = requests.Session()

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.fleet.api_key}"}
        if self.fleet.enroll_key:
            h["X-Enroll-Key"] = self.fleet.enroll_key
        return h

    def _send_heartbeat(self) -> list[dict]:
        payload = {"box_id": self.fleet.box_id, "ts": int(time.time()), **collect_status(self.cfg)}
        r = self._session.post(
            f"{self.fleet.server_url}/api/heartbeat",
            json=payload, headers=self._headers(), timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("commands", [])

    def _report_result(self, cmd_id, ok: bool, output: str) -> None:
        try:
            self._session.post(
                f"{self.fleet.server_url}/api/command-result",
                json={"box_id": self.fleet.box_id, "command_id": cmd_id, "ok": ok, "output": output},
                headers=self._headers(), timeout=15,
            )
        except requests.RequestException as e:
            log.warning("could not report command result: %s", e)

    def _handle_commands(self, commands: list[dict]) -> None:
        for cmd in commands:
            cmd_id, cmd_type = cmd.get("id"), cmd.get("type")
            ok, output = _run_command(cmd_type)
            self._report_result(cmd_id, ok, output)
            # reboot won't return; report happens before the box goes down.

    def run_forever(self) -> None:
        import signal

        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        log.info("fleet agent started: box=%s -> %s every %ss",
                 self.fleet.box_id, self.fleet.server_url, self.fleet.interval)
        while not self._stop.is_set():
            try:
                commands = self._send_heartbeat()
                if commands:
                    log.info("received %d command(s)", len(commands))
                    self._handle_commands(commands)
            except requests.RequestException as e:
                log.warning("heartbeat failed: %s", e)
            self._stop.wait(self.fleet.interval)
        log.info("fleet agent stopped")
