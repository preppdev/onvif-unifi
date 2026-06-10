#!/usr/bin/env bash
#
# ONVIF Gateway installer for Debian/Ubuntu.
#
# One-liner (after the repo is public):
#   curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh | sudo bash
#
# Idempotent: safe to re-run to update. Installs system deps + MediaMTX, clones
# the repo to /opt/onvif-gateway, builds a venv, and installs the systemd unit.
# It does NOT start the service — you must edit the config first (see the end).
set -euo pipefail

# ---- configurable via environment ------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/preppdev/onvif-unifi.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/onvif-gateway}"
CONFIG_DIR="${CONFIG_DIR:-/etc/onvif-gateway}"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-v1.18.1}"
# ----------------------------------------------------------------------------

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[install] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
command -v apt-get >/dev/null || die "this installer supports Debian/Ubuntu (apt) only"

# 1. System dependencies ------------------------------------------------------
log "installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    ffmpeg iproute2 isc-dhcp-client curl ca-certificates git tar

# 2. MediaMTX binary ----------------------------------------------------------
install_mediamtx() {
    if command -v mediamtx >/dev/null && [ "${FORCE_MEDIAMTX:-0}" != "1" ]; then
        log "mediamtx already present ($(command -v mediamtx)); skipping"
        return
    fi
    local arch tarball url tmp
    case "$(uname -m)" in
        x86_64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        armv7l)  arch="armv7" ;;
        *) die "unsupported CPU arch: $(uname -m)" ;;
    esac
    tarball="mediamtx_${MEDIAMTX_VERSION}_linux_${arch}.tar.gz"
    url="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${tarball}"
    tmp="$(mktemp -d)"
    log "downloading MediaMTX ${MEDIAMTX_VERSION} (${arch})"
    curl -fsSL "$url" -o "$tmp/$tarball" || die "MediaMTX download failed: $url"
    tar -xzf "$tmp/$tarball" -C "$tmp"
    install -m 0755 "$tmp/mediamtx" /usr/local/bin/mediamtx
    rm -rf "$tmp"
    log "mediamtx -> /usr/local/bin/mediamtx"
}
install_mediamtx

# 3. Clone or update the repo -------------------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    log "updating existing checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"
elif [ -f "$(dirname "$0")/pyproject.toml" ] && [ "${USE_LOCAL:-0}" = "1" ]; then
    log "installing from local source"
    mkdir -p "$INSTALL_DIR"
    cp -a "$(cd "$(dirname "$0")" && pwd)/." "$INSTALL_DIR/"
else
    log "cloning $REPO_URL ($BRANCH) -> $INSTALL_DIR"
    [ -z "$REPO_URL" ] && \
        die "set REPO_URL to your repository (or run with USE_LOCAL=1 from a checkout)"
    rm -rf "$INSTALL_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# 4. Python virtualenv --------------------------------------------------------
log "building virtualenv"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR"

# 5. Config / provisioning mode ----------------------------------------------
# APPLIANCE MODE (set FLEET_SERVER_URL + FLEET_ENROLL_KEY): provision in-hand for
# remote programming. Writes the fleet bootstrap, ships with NO site config — the
# camera config is pushed later from the fleet dashboard once the box is in the
# field. Otherwise: manual mode (drop an editable gateway.yaml).
mkdir -p "$CONFIG_DIR"
APPLIANCE=0
if [ -n "${FLEET_SERVER_URL:-}" ] && [ -n "${FLEET_ENROLL_KEY:-}" ]; then
    APPLIANCE=1
    log "appliance mode: writing fleet bootstrap (camera config comes from server)"
    cat > "$CONFIG_DIR/fleet.yaml" <<FLEET
enabled: true
server_url: ${FLEET_SERVER_URL}
enroll_key: ${FLEET_ENROLL_KEY}
interval: ${FLEET_INTERVAL:-60}
FLEET
    chmod 600 "$CONFIG_DIR/fleet.yaml"
    rm -f "$CONFIG_DIR/gateway.yaml"          # site config is server-managed
elif [ ! -f "$CONFIG_DIR/gateway.yaml" ]; then
    cp "$INSTALL_DIR/config.example.yaml" "$CONFIG_DIR/gateway.yaml"
    chmod 600 "$CONFIG_DIR/gateway.yaml"
    log "created $CONFIG_DIR/gateway.yaml (from example) — EDIT THIS"
else
    log "keeping existing $CONFIG_DIR/gateway.yaml"
fi

# 6. Tailscale (optional) -----------------------------------------------------
# Pass TAILSCALE_AUTHKEY=tskey-... to install Tailscale and join now. A REUSABLE
# key is also saved for firstboot so cloned golden images auto-join on first boot.
if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
    if ! command -v tailscale >/dev/null; then
        log "installing Tailscale"
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    mkdir -p "$CONFIG_DIR"
    printf '%s' "$TAILSCALE_AUTHKEY" > "$CONFIG_DIR/tailscale.authkey"
    chmod 600 "$CONFIG_DIR/tailscale.authkey"
    # Name the node onvif-<MAC> to match the agent's box_id (stable + unique).
    ts_mac="$(cat /sys/class/net/"$(ip route show default | awk '{print $5; exit}')"/address 2>/dev/null | tr -d ':')"
    log "joining tailnet as onvif-${ts_mac:-$(hostname)}"
    tailscale up --authkey "$TAILSCALE_AUTHKEY" --hostname "onvif-${ts_mac:-$(hostname)}" || \
        warn "tailscale up failed; join manually later"
fi

# 7. systemd units ------------------------------------------------------------
log "installing systemd units"
for unit in onvif-gateway onvif-agent onvif-firstboot; do
    sed "s#/opt/onvif-gateway#${INSTALL_DIR}#g; s#/etc/onvif-gateway#${CONFIG_DIR}#g" \
        "$INSTALL_DIR/systemd/${unit}.service" > "/etc/systemd/system/${unit}.service"
done
systemctl daemon-reload

if [ "$APPLIANCE" = "1" ]; then
    # Agent on (registers + phones home now); gateway off until config is pushed.
    systemctl enable --now onvif-agent >/dev/null 2>&1 || true
    systemctl enable onvif-firstboot >/dev/null 2>&1 || true
    systemctl disable onvif-gateway >/dev/null 2>&1 || true
    cat <<EOF

$(printf '\033[1;32m✓ Appliance provisioned and registered\033[0m')

This box is ready to ship. It has joined Tailscale and the fleet agent is running;
it should appear in your dashboard as UNPROVISIONED within ~1 minute:
  ${FLEET_SERVER_URL}

In the field: power it on (wired network); it rejoins Tailscale and phones home.
Then push its camera config from the dashboard — the gateway starts automatically.
EOF
else
    cat <<EOF

$(printf '\033[1;32m✓ ONVIF Gateway installed\033[0m')

Next steps:
  1. Edit your config:
       sudo \$EDITOR ${CONFIG_DIR}/gateway.yaml
  2. Pull the real RTSP URLs from the encoder:
       ${INSTALL_DIR}/.venv/bin/onvif-gateway -c ${CONFIG_DIR}/gateway.yaml provision
  3. Validate, then start:
       ${INSTALL_DIR}/.venv/bin/onvif-gateway -c ${CONFIG_DIR}/gateway.yaml validate
       sudo systemctl enable --now onvif-gateway
  4. Watch it:
       journalctl -u onvif-gateway -f
  5. (Optional) fleet heartbeat — set the [fleet] block in the config, then:
       sudo systemctl enable --now onvif-agent

To update later:  sudo ${INSTALL_DIR}/scripts/update.sh
EOF
fi
