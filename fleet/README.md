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

## Run

Needs Python 3.9+ and Flask (`pip install flask`). Set env and start:

```bash
export FLEET_ADMIN_PASSWORD='choose-a-strong-one'   # dashboard login (user: admin)
export FLEET_ENROLL_KEY='shared-secret-for-new-boxes'
export FLEET_DB=/var/lib/fleet/fleet.db
python -m fleet                                     # listens on :8080
```

For production, run behind TLS (the agent talks HTTPS) and a real WSGI server:

```bash
pip install gunicorn
gunicorn -w 2 -b 127.0.0.1:8080 fleet.server:app
# then put Caddy/nginx in front for HTTPS on your domain
```

A $5 VPS with a domain + Caddy (automatic TLS) is plenty. **Always serve over
HTTPS** — heartbeats carry box API keys.

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
