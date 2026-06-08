#!/usr/bin/env bash
# Pull the latest code and restart the service. Config is left untouched.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/onvif-gateway}"
BRANCH="${BRANCH:-main}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }
[ -d "$INSTALL_DIR/.git" ] || { echo "no checkout at $INSTALL_DIR; run install.sh first"; exit 1; }

echo "[update] fetching origin/$BRANCH"
git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"

echo "[update] syncing python deps"
"$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR"

# Refresh the unit in case it changed.
sed "s#/opt/onvif-gateway#${INSTALL_DIR}#g" \
    "$INSTALL_DIR/systemd/onvif-gateway.service" > /etc/systemd/system/onvif-gateway.service
systemctl daemon-reload

if systemctl is-enabled --quiet onvif-gateway 2>/dev/null; then
    echo "[update] restarting service"
    systemctl restart onvif-gateway
fi
echo "[update] done -> $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
