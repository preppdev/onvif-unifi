#!/usr/bin/env bash
# Turn a configured reference box into a clean golden master, ready to clone.
#
# Run this LAST on the reference mini, then power off and image the disk. Every
# clone will boot, auto-join Tailscale (firstboot), self-register with a unique
# MAC-derived box_id, and wait to be provisioned from the dashboard.
#
# Prereqs already done on the reference box:
#   - gateway + agent installed (install.sh)
#   - /etc/onvif-gateway/fleet.yaml written (server_url + enroll_key)
#   - reusable Tailscale auth key saved to /etc/onvif-gateway/tailscale.authkey
set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-/etc/onvif-gateway}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "[prepare-image] checks"
[ -f "$CONFIG_DIR/fleet.yaml" ]        || { echo "missing $CONFIG_DIR/fleet.yaml"; exit 1; }
[ -s "$CONFIG_DIR/tailscale.authkey" ] || echo "WARN: no reusable key at $CONFIG_DIR/tailscale.authkey — clones won't auto-join"

echo "[prepare-image] service states: agent+firstboot on, gateway off (no cameras until provisioned)"
systemctl enable onvif-agent onvif-firstboot >/dev/null 2>&1 || true
systemctl disable --now onvif-gateway >/dev/null 2>&1 || true

echo "[prepare-image] removing site config + per-box identity"
rm -f "$CONFIG_DIR/gateway.yaml"                                  # camera config is pushed per site
rm -f /var/lib/onvif-gateway/api_key /var/lib/onvif-gateway/applied_version
rm -rf "$CONFIG_DIR/../onvif-gateway"/*.tmp 2>/dev/null || true

echo "[prepare-image] tailscale logout (clones re-auth via the baked key on first boot)"
tailscale logout >/dev/null 2>&1 || true

echo "[prepare-image] reset machine-id (regenerated uniquely per clone)"
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id 2>/dev/null || true

echo "[prepare-image] clearing logs + shell history"
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -f /root/.bash_history /home/*/.bash_history 2>/dev/null || true

cat <<EOF

[prepare-image] DONE. This box is a golden master.
Next: power off and image the disk (clone the SSD, or dd/Clonezilla to a file).
Each clone will, on first boot: join Tailscale, self-register, and appear in the
dashboard as 'unprovisioned' for remote programming.
EOF
