"""Fleet heartbeat + zero-touch provisioning agent.

Runs as its own service, independent of the gateway, so a freshly-imaged box with
NO camera config still phones home and can be programmed remotely. Each box:

  1. Derives a stable unique box_id from its hardware (NIC MAC) — one golden image
     clones to every box, no per-box editing.
  2. Self-registers with the fleet server using a shared enroll key.
  3. Reports status every `interval` seconds (provisioned?, per-camera health, ...).
  4. Pulls its desired gateway config from the server; when it changes, writes it
     and (re)starts the gateway. An unprovisioned box just waits here.
  5. Executes queued commands from a FIXED allowlist (reboot/update/restart/...).

Security: outbound-only, per-box bearer key over the (Tailscale-encrypted) link,
and the command set is hard-coded here — the server can never run arbitrary shell.
"""
from __future__ import annotations

import logging
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import requests
import yaml

from .config import FleetConfig, load_config

log = logging.getLogger("agent")

ALLOWED_COMMANDS = {"reboot", "update", "restart_gateway", "stop_gateway", "start_gateway"}
GATEWAY_SERVICE = "onvif-gateway"
STATE_DIR = "/var/lib/onvif-gateway"
DEFAULT_FLEET_CONFIG = "/etc/onvif-gateway/fleet.yaml"
DEFAULT_GATEWAY_CONFIG = "/etc/onvif-gateway/gateway.yaml"


# --------------------------------------------------------------------------- #
# bootstrap / identity
# --------------------------------------------------------------------------- #
def load_fleet(path: str) -> FleetConfig:
    """Load fleet bootstrap settings. Accepts a dedicated fleet.yaml (settings at
    top level) or a full gateway.yaml (settings under a `fleet:` key)."""
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    s = raw.get("fleet", raw)
    return FleetConfig(
        enabled=bool(s.get("enabled", True)),
        server_url=str(s.get("server_url", "")).rstrip("/"),
        box_id=s.get("box_id", ""),
        api_key=s.get("api_key", ""),
        enroll_key=s.get("enroll_key", ""),
        interval=int(s.get("interval", 60)),
        gateway_config_path=s.get("gateway_config_path", DEFAULT_GATEWAY_CONFIG),
    )


def derive_box_id() -> str:
    """Stable, unique per-hardware id from the primary NIC MAC."""
    return f"onvif-{uuid.getnode():012x}"


def persistent_api_key() -> str:
    """Per-box key, generated once and persisted (survives reboots; only lost on
    a disk wipe, which means re-imaging = a new identity anyway)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    p = Path(STATE_DIR) / "api_key"
    if p.exists():
        return p.read_text().strip()
    key = secrets.token_hex(20)
    p.write_text(key)
    os.chmod(p, 0o600)
    return key


def _applied_version() -> str:
    p = Path(STATE_DIR) / "applied_version"
    return p.read_text().strip() if p.exists() else ""


def _set_applied_version(v: str) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    (Path(STATE_DIR) / "applied_version").write_text(v)


# --------------------------------------------------------------------------- #
# host facts
# --------------------------------------------------------------------------- #
def _repo_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _version() -> str:
    try:
        out = subprocess.run(["git", "-C", str(_repo_dir()), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
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
        ips = out.stdout.strip().splitlines()
        return ips[0] if ips else None
    except (OSError, subprocess.SubprocessError):
        return None


def _service_active(name: str) -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _camera_health(gateway_config_path: str) -> tuple[bool, list[dict]]:
    """(provisioned, cameras). Unprovisioned boxes have no valid config yet."""
    try:
        cfg = load_config(gateway_config_path)
    except Exception:  # noqa: BLE001 - missing/invalid config == not provisioned
        return False, []
    cams = []
    for cam in cfg.cameras:
        try:
            up = requests.get(f"http://{cam.ip}:{cam.onvif_port}/healthz", timeout=2).ok
        except requests.RequestException:
            up = False
        cams.append({"id": cam.id, "name": cam.name, "ip": cam.ip, "up": up})
    return True, cams


def collect_status(gateway_config_path: str) -> dict:
    provisioned, cams = _camera_health(gateway_config_path)
    return {
        "version": _version(),
        "hostname": platform.node(),
        "uptime_s": _uptime_s(),
        "tailscale_ip": _tailscale_ip(),
        "provisioned": provisioned,
        "gateway_active": _service_active(GATEWAY_SERVICE),
        "applied_version": _applied_version(),
        "cameras_total": len(cams),
        "cameras_up": sum(1 for c in cams if c["up"]),
        "cameras": cams,
    }


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def apply_config(text: str, version: str, gateway_config_path: str) -> None:
    """Write the server-pushed gateway config and (re)start the gateway."""
    os.makedirs(os.path.dirname(gateway_config_path) or ".", exist_ok=True)
    tmp = gateway_config_path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, gateway_config_path)
    log.info("applied config version %s; (re)starting gateway", version)
    subprocess.run(["systemctl", "enable", GATEWAY_SERVICE], capture_output=True, timeout=30)
    subprocess.run(["systemctl", "restart", GATEWAY_SERVICE], capture_output=True, timeout=60)
    _set_applied_version(version)


def _run_command(cmd_type: str) -> tuple[bool, str]:
    if cmd_type not in ALLOWED_COMMANDS:
        return False, f"refused: {cmd_type} not in allowlist"
    actions = {
        "restart_gateway": ["systemctl", "restart", GATEWAY_SERVICE],
        "stop_gateway": ["systemctl", "stop", GATEWAY_SERVICE],
        "start_gateway": ["systemctl", "start", GATEWAY_SERVICE],
        "update": [str(_repo_dir() / "scripts" / "update.sh")],
        "reboot": ["systemctl", "reboot"],
    }
    log.info("executing command: %s", cmd_type)
    try:
        out = subprocess.run(actions[cmd_type], capture_output=True, text=True, timeout=300)
        return out.returncode == 0, (out.stdout + out.stderr)[-2000:]
    except subprocess.SubprocessError as e:
        return False, str(e)


# --------------------------------------------------------------------------- #
# agent loop
# --------------------------------------------------------------------------- #
class FleetAgent:
    def __init__(self, fleet: FleetConfig):
        if not fleet.enabled:
            raise ValueError("fleet disabled in config")
        if not fleet.server_url:
            raise ValueError("fleet.server_url is required")
        self.fleet = fleet
        self.box_id = fleet.box_id or derive_box_id()
        self.api_key = fleet.api_key or persistent_api_key()
        self._stop = threading.Event()
        self._session = requests.Session()

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}"}
        if self.fleet.enroll_key:
            h["X-Enroll-Key"] = self.fleet.enroll_key
        return h

    def _heartbeat(self) -> dict:
        payload = {"box_id": self.box_id, "ts": int(time.time()),
                   **collect_status(self.fleet.gateway_config_path)}
        r = self._session.post(f"{self.fleet.server_url}/api/heartbeat",
                               json=payload, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def _report_result(self, cmd_id, ok: bool, output: str) -> None:
        try:
            self._session.post(f"{self.fleet.server_url}/api/command-result",
                               json={"box_id": self.box_id, "command_id": cmd_id, "ok": ok, "output": output},
                               headers=self._headers(), timeout=15)
        except requests.RequestException as e:
            log.warning("could not report command result: %s", e)

    def _handle_response(self, data: dict) -> None:
        # 1. Config push — apply if the server's desired version differs from ours.
        desired = data.get("config_version")
        config_text = data.get("config")
        if config_text and desired and desired != _applied_version():
            try:
                apply_config(config_text, desired, self.fleet.gateway_config_path)
            except Exception as e:  # noqa: BLE001
                log.error("failed to apply config: %s", e)
        # 2. Command queue.
        for cmd in data.get("commands", []):
            ok, output = _run_command(cmd.get("type"))
            self._report_result(cmd.get("id"), ok, output)

    def run_forever(self) -> None:
        import signal

        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())
        log.info("fleet agent started: box=%s -> %s every %ss",
                 self.box_id, self.fleet.server_url, self.fleet.interval)
        while not self._stop.is_set():
            try:
                self._handle_response(self._heartbeat())
            except requests.RequestException as e:
                log.warning("heartbeat failed: %s", e)
            self._stop.wait(self.fleet.interval)
        log.info("fleet agent stopped")
