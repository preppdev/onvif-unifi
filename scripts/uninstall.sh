#!/usr/bin/env bash
# Remove the service and code. Keeps /etc/onvif-gateway/gateway.yaml unless --purge.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/onvif-gateway}"
CONFIG_DIR="${CONFIG_DIR:-/etc/onvif-gateway}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "[uninstall] stopping services"
systemctl disable --now onvif-gateway onvif-agent 2>/dev/null || true

# Best-effort: tear down any leftover virtual NICs from the installed config.
if [ -x "$INSTALL_DIR/.venv/bin/onvif-gateway" ] && [ -f "$CONFIG_DIR/gateway.yaml" ]; then
    "$INSTALL_DIR/.venv/bin/onvif-gateway" -c "$CONFIG_DIR/gateway.yaml" down 2>/dev/null || true
fi

rm -f /etc/systemd/system/onvif-gateway.service /etc/systemd/system/onvif-agent.service
systemctl daemon-reload
rm -rf "$INSTALL_DIR"

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$CONFIG_DIR"
    echo "[uninstall] removed config too (--purge)"
else
    echo "[uninstall] kept $CONFIG_DIR (use --purge to remove)"
fi
echo "[uninstall] done"
