#!/usr/bin/env bash
# First-boot Tailscale enrollment for cloned golden images.
#
# Each clone joins the tailnet using a baked-in REUSABLE auth key, with a unique
# hostname derived from its NIC MAC (matching the agent's box_id). Idempotent:
# does nothing once the box is already connected. After a successful first join
# the key file is removed, so deployed boxes don't retain the reusable key.
set -euo pipefail

KEY_FILE="${KEY_FILE:-/etc/onvif-gateway/tailscale.authkey}"

command -v tailscale >/dev/null || { echo "[firstboot] tailscale not installed"; exit 0; }

# Already on the tailnet? nothing to do.
if tailscale status >/dev/null 2>&1; then
    echo "[firstboot] already connected to tailnet"
    exit 0
fi

[ -s "$KEY_FILE" ] || { echo "[firstboot] no auth key at $KEY_FILE; skipping"; exit 0; }

# box_id == onvif-<mac>; reuse it as the tailscale hostname for consistency.
mac="$(cat /sys/class/net/"$(ip route show default | awk '{print $5; exit}')"/address 2>/dev/null | tr -d ':')"
host="onvif-${mac:-$(hostname)}"

echo "[firstboot] joining tailnet as $host"
if tailscale up --authkey "$(cat "$KEY_FILE")" --hostname "$host"; then
    rm -f "$KEY_FILE"      # consumed; don't leave the reusable key on a deployed box
    echo "[firstboot] joined; removed baked-in key"
else
    echo "[firstboot] join failed; will retry next boot"
fi
