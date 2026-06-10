# ONVIF Gateway

Turns each channel of a **Hanwha Wisenet SPE-1630** 16-channel encoder into a
**discrete virtual ONVIF camera** — each with its own IP and MAC — so **UniFi
Protect** adopts them as 16 separate cameras instead of one multi-channel device.

```
HD-TVI cams ─coax─► SPE-1630 ─RTSP(H.264)─► [ onvif-gateway ] ─16× ONVIF/RTSP─► UniFi Protect
                    one IP/MAC                Linux host           16 IPs/MACs
```

## How it works

Per channel, the gateway stands up an independent "fake camera":

- **`gateway/vnic.py`** — a macvlan virtual NIC with a unique IP (static) and a
  deterministic locally-administered MAC (`02:…`). The ARP-isolation sysctls
  (`arp_ignore=1`, `arp_announce=2`) are what keep 16 IPs on one physical NIC
  stable instead of flapping.
- **`gateway/mediamtx.py`** — one MediaMTX instance re-serves every channel's
  RTSP. **Copy-mode by default** (zero re-encode — the SPE-1630 already outputs
  H.264) with on-demand pull + auto-reconnect. Transcode is opt-in per profile.
- **`gateway/onvif/`** — per camera: an ONVIF Device + Media SOAP service bound
  to the camera's virtual IP, plus a WS-Discovery responder. `GetStreamUri`
  hands UniFi `rtsp://<virtual-ip>:8554/…`, so video appears to come from the
  same unique identity it discovered.
- **`gateway/supervisor.py`** — brings up VNICs → MediaMTX → ONVIF, runs a
  watchdog that restarts a dead MediaMTX or ONVIF server, and tears down all
  VNICs cleanly on shutdown.

## Install (Debian/Ubuntu)

One line on the target box — installs system deps + MediaMTX, clones to
`/opt/onvif-gateway`, builds a venv, and installs the systemd unit:

```bash
curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh | sudo bash
```

Then edit `/etc/onvif-gateway/gateway.yaml`, `provision` to pull the encoder's real
RTSP URLs, and `sudo systemctl enable --now onvif-gateway`. Update later with
`sudo /opt/onvif-gateway/scripts/update.sh`. (Manual setup is below.)

Running on an **Intel Mac mini**? See [docs/mac-mini-linux.md](docs/mac-mini-linux.md)
— bare-metal Ubuntu runs the gateway unchanged.

### Provision a field appliance (2 steps)

For boxes you provision in-hand and configure remotely once they're on site:

1. Install Ubuntu Server.
2. Run the build line — installs everything, joins Tailscale, writes the fleet
   bootstrap, and starts the agent so the box registers immediately:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh | sudo \
     TAILSCALE_AUTHKEY='tskey-auth-REUSABLE-...' \
     FLEET_SERVER_URL='https://www.digitaltidewater.com' \
     FLEET_ENROLL_KEY='your-shared-enroll-key' \
     bash
   ```

The box ships with **no site config** and the gateway disabled. It appears in the
fleet dashboard as **unprovisioned**; when it's powered on in the field it rejoins
Tailscale and phones home, and you push its camera config from the dashboard — the
gateway starts automatically. (Setting `FLEET_SERVER_URL` + `FLEET_ENROLL_KEY`
is what enables this "appliance mode"; omit them for a manually-configured box.)

## Requirements (Linux target)

- Python ≥ 3.9
- [`mediamtx`](https://github.com/bluenviron/mediamtx/releases) on `PATH`
- `ffmpeg` (only for snapshots and opt-in transcoding)
- `iproute2` (`ip`), root or `CAP_NET_ADMIN`
- On Proxmox/ESXi VMs: **enable promiscuous mode** on the vSwitch/port group.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
cp config.example.yaml gateway.yaml      # then edit: encoder, IPs, parent NIC
.venv/bin/onvif-gateway -c gateway.yaml validate
```

### Get the real RTSP URLs off the encoder

The channel→profile mapping in the SPE-1630's RTSP URLs isn't always 1:1, so
ask the encoder directly and paste the result into `gateway.yaml`:

```bash
.venv/bin/onvif-gateway -c gateway.yaml provision
```

### Run

```bash
sudo .venv/bin/onvif-gateway -c gateway.yaml up      # foreground
.venv/bin/onvif-gateway -c gateway.yaml status       # ping each camera
sudo .venv/bin/onvif-gateway -c gateway.yaml down    # remove all VNICs
```

### Install as a service

```bash
sudo cp systemd/onvif-gateway.service /etc/systemd/system/
sudo mkdir -p /etc/onvif-gateway && sudo cp gateway.yaml /etc/onvif-gateway/
sudo systemctl enable --now onvif-gateway
```

In UniFi Protect, the 16 cameras should appear under **third-party ONVIF**
adoption; adopt each with the `onvif_username`/`onvif_password` from your config.

## Remote fleet management

For appliances deployed at client sites (behind NAT):

- **Access** — install [Tailscale](https://tailscale.com) so you can SSH in / run
  the updater from anywhere. The installer auto-joins if you pass an auth key:
  `curl -fsSL …/install.sh | sudo TAILSCALE_AUTHKEY=tskey-… bash`.
- **Heartbeat + control** — the optional `onvif-agent` service phones home to a
  central [fleet server](fleet/README.md) with per-camera health, version, and
  uptime, and executes queued commands (restart / update / reboot). Enable the
  `fleet:` block in the config, then `sudo systemctl enable --now onvif-agent`.

## CLI

| Command | Purpose |
|---|---|
| `validate` | Load config, print resolved cameras (IP/MAC/source) |
| `provision` | ONVIF-probe the encoder, print real per-channel RTSP URLs |
| `up` | Bring up all virtual cameras (foreground; SIGTERM cleans up) |
| `down` | Remove all virtual NICs |
| `status` | Ping each camera's `/healthz` |
| `agent` | Run the fleet heartbeat agent (foreground; via `onvif-agent` service) |

## Status

Phases 0–3 complete and unit-tested (`pytest tests/`, 27 tests, no hardware needed):
scaffold, network (VNIC), RTSP relay, ONVIF device + discovery.

ONVIF surface covers the full UniFi adoption path: device info / capabilities /
services / scopes / network interfaces, media profiles + stream/snapshot URIs,
video source & encoder configurations (+ options), a minimal pull-point **Events**
service, and **no-op `Set*`** handlers so UniFi's config push succeeds. Unknown
operations are logged at INFO so coverage can be extended against real traffic.

Provisioning and the reliability watchdog have working first cuts. Not yet built:
boot-time re-provision hardening, optional status web UI, real motion events.
