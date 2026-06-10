# Fleet server

Central dashboard for ONVIF-gateway appliances. Boxes phone home (outbound only,
so they work behind client NAT) with a heartbeat; you see status and queue remote
commands. Pairs with the `onvif-agent` service on each box.

## What it shows / does

- **Fleet view**: each box online/offline, version, `N/16 cameras up`, gateway
  service state, Tailscale IP, last-seen.
- **Per-box detail**: each camera's ONVIF health + recent command history.
- **Remote control**: queue `restart_gateway`, `update`, `reboot`, `stop_gateway`,
  `start_gateway`. The box picks the command up on its next heartbeat and runs it.
  Commands are a fixed allowlist — never arbitrary shell.

## Run on-prem over Tailscale (recommended)

Put this server on any always-on machine at your office and join it to your
tailnet. Appliances reach it over the WireGuard tunnel, so **no public host, no
domain, and no TLS certs are needed** — Tailscale already encrypts everything.

```bash
cd fleet
cp .env.example .env          # set FLEET_ADMIN_PASSWORD + FLEET_ENROLL_KEY
docker compose up -d
sudo tailscale up             # if this host isn't on the tailnet yet
```

Name the host `fleet` (or use its `100.x` address) and point each box's config at
it: `fleet.server_url: http://fleet:8080`. Dashboard: `http://fleet:8080` from any
device on your tailnet (user `admin`).

> Because the link is tailnet-only, plain HTTP is fine. If you *do* expose it to
> the public internet instead, put it behind Caddy/nginx for HTTPS — heartbeats
> carry box API keys.

### Without Docker

```bash
pip install flask gunicorn
FLEET_ADMIN_PASSWORD=… FLEET_ENROLL_KEY=… FLEET_DB=/var/lib/fleet/fleet.db \
  gunicorn -w 2 -b 0.0.0.0:8080 fleet.server:app
```

A tiny VM (1 vCPU / 1 GB) is more than enough — it's just Flask + SQLite.

## Enrolling a box

1. Pick a unique `box_id` and a per-box `api_key`.
2. Put them (plus your `enroll_key` and `server_url`) in the box's
   `/etc/onvif-gateway/gateway.yaml` under `fleet:` (see `config.example.yaml`).
3. `sudo systemctl enable --now onvif-agent` on the box.

On its first heartbeat the box registers itself (the `enroll_key` authorizes new
box_ids). After that, only its `api_key` is accepted for that `box_id`, and the
enroll key is no longer needed. Rotate a box's key by updating both the DB row
and the box config.

## Environment

| Var | Default | Purpose |
|---|---|---|
| `FLEET_ADMIN_PASSWORD` | — (required) | dashboard basic-auth password (user `admin`) |
| `FLEET_ENROLL_KEY` | — | key required to register a new box_id |
| `FLEET_DB` | `fleet.db` | SQLite path |
| `FLEET_OFFLINE_AFTER` | `180` | seconds without heartbeat → shown offline |
| `FLEET_PORT` | `8080` | listen port |

## Security notes

- Outbound-only agents; the server is the only thing exposed, behind TLS.
- API keys are stored plaintext in SQLite today — fine for a controlled rollout;
  hash them if the DB host isn't trusted.
- The command set is fixed in code on **both** sides (`ALLOWED_COMMANDS`); the
  server cannot make a box run anything outside it.

> **Deprecated (2026-06-10):** the fleet control plane moved into the Digital
> Tidewater site (tech.digitaltidewater.com, Neon/Prisma). Boxes now phone home to
> https://www.digitaltidewater.com/api/fleet/*. This standalone Flask server is kept
> for reference only.
