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

ALLOWED_COMMANDS = {
    "reboot", "update", "restart_gateway", "stop_gateway", "start_gateway",
    "tunnel_on", "tunnel_off",  # toggle the box as a Tailscale subnet router into its LAN
}
GATEWAY_SERVICE = "onvif-gateway"
STATE_DIR = "/var/lib/onvif-gateway"
DEFAULT_FLEET_CONFIG = "/etc/onvif-gateway/fleet.yaml"
DEFAULT_GATEWAY_CONFIG = "/etc/onvif-gateway/gateway.yaml"

# Network discovery + WAN IP are heavier; refresh at most this often.
_DISCOVERY_TTL = 300
_cache: dict = {"discovery": None, "discovery_ts": 0, "wan_ip": None, "wan_ts": 0}


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


def _default_iface() -> Optional[str]:
    try:
        out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        parts = out.stdout.split()
        return parts[parts.index("dev") + 1] if "dev" in parts else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _lan_ip() -> Optional[str]:
    from .vnic import current_ip

    iface = _default_iface()
    return current_ip(iface) if iface else None


def _lan_cidr() -> Optional[str]:
    """The box's LAN subnet in CIDR (for advertising as a Tailscale route)."""
    iface = _default_iface()
    if not iface:
        return None
    try:
        out = subprocess.run(["ip", "-4", "-o", "route", "show", "dev", iface, "scope", "link"],
                             capture_output=True, text=True, timeout=5)
        for tok in out.stdout.split():
            if "/" in tok and tok.count(".") == 3:
                return tok
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _wan_ip() -> Optional[str]:
    now = time.time()
    if _cache["wan_ip"] and now - _cache["wan_ts"] < _DISCOVERY_TTL:
        return _cache["wan_ip"]
    for url in ("https://api.ipify.org", "https://ifconfig.co/ip", "https://icanhazip.com"):
        try:
            ip = requests.get(url, timeout=5).text.strip()
            if ip and ip.count(".") == 3:
                _cache["wan_ip"], _cache["wan_ts"] = ip, now
                return ip
        except requests.RequestException:
            continue
    return _cache["wan_ip"]


def _discovery() -> dict:
    now = time.time()
    if _cache["discovery"] is not None and now - _cache["discovery_ts"] < _DISCOVERY_TTL:
        return _cache["discovery"]
    try:
        from .discover import discover_all
        _cache["discovery"] = discover_all()
    except Exception as e:  # noqa: BLE001
        log.debug("discovery failed: %s", e)
        _cache["discovery"] = _cache["discovery"] or {}
    _cache["discovery_ts"] = now
    return _cache["discovery"]


def _tunnel_routes() -> str:
    """Currently-advertised Tailscale subnet routes (empty = tunnel off)."""
    p = Path(STATE_DIR) / "tunnel_routes"
    return p.read_text().strip() if p.exists() else ""


def _camera_health(gateway_config_path: str) -> tuple[bool, list[dict]]:
    """(provisioned, cameras). Reads each camera's actual VNIC IP (ground truth for
    both static and DHCP) so leased addresses surface in the dashboard."""
    from .vnic import current_ip

    try:
        cfg = load_config(gateway_config_path)
    except Exception:  # noqa: BLE001 - missing/invalid config == not provisioned
        return False, []
    cams = []
    for cam in cfg.cameras:
        ip = current_ip(cam.vnic_name) or cam.ip
        up = False
        if ip:
            try:
                up = requests.get(f"http://{ip}:{cam.onvif_port}/healthz", timeout=2).ok
            except requests.RequestException:
                up = False
        cams.append({"id": cam.id, "name": cam.name, "ip": ip, "ip_mode": cam.ip_mode, "up": up})
    return True, cams


def collect_status(gateway_config_path: str) -> dict:
    provisioned, cams = _camera_health(gateway_config_path)
    return {
        "version": _version(),
        "hostname": platform.node(),
        "uptime_s": _uptime_s(),
        "tailscale_ip": _tailscale_ip(),
        "lan_ip": _lan_ip(),
        "wan_ip": _wan_ip(),
        "tunnel_routes": _tunnel_routes(),
        "provisioned": provisioned,
        "gateway_active": _service_active(GATEWAY_SERVICE),
        "applied_version": _applied_version(),
        "cameras_total": len(cams),
        "cameras_up": sum(1 for c in cams if c["up"]),
        "cameras": cams,
        "discovered": _discovery(),
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


def _set_tunnel(on: bool) -> tuple[bool, str]:
    """Toggle the box as a Tailscale subnet router into its own LAN."""
    if on:
        cidr = _lan_cidr()
        if not cidr:
            return False, "could not determine LAN subnet"
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True, timeout=10)
        r = subprocess.run(["tailscale", "set", f"--advertise-routes={cidr}"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            os.makedirs(STATE_DIR, exist_ok=True)
            (Path(STATE_DIR) / "tunnel_routes").write_text(cidr)
            return True, f"advertising {cidr} (approve the route in the Tailscale admin once)"
        return False, (r.stdout + r.stderr)[-500:]
    else:
        r = subprocess.run(["tailscale", "set", "--advertise-routes="],
                           capture_output=True, text=True, timeout=30)
        (Path(STATE_DIR) / "tunnel_routes").write_text("")
        return r.returncode == 0, (r.stdout + r.stderr)[-500:] or "tunnel off"


def _run_command(cmd_type: str) -> tuple[bool, str]:
    if cmd_type not in ALLOWED_COMMANDS:
        return False, f"refused: {cmd_type} not in allowlist"
    if cmd_type == "tunnel_on":
        return _set_tunnel(True)
    if cmd_type == "tunnel_off":
        return _set_tunnel(False)
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
        r = self._session.post(f"{self.fleet.server_url}/api/fleet/heartbeat",
                               json=payload, headers=self._headers(), timeout=15)
        r.raise_for_status()
        return r.json()

    def _report_result(self, cmd_id, ok: bool, output: str) -> None:
        try:
            self._session.post(f"{self.fleet.server_url}/api/fleet/command-result",
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
